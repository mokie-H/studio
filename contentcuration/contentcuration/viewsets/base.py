import traceback

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import Http404
from django_bulk_update.helper import bulk_update
from django_filters.constants import EMPTY_VALUES
from django_filters.rest_framework import FilterSet
from rest_framework.generics import get_object_or_404
from rest_framework.mixins import DestroyModelMixin
from rest_framework.response import Response
from rest_framework.serializers import ListSerializer
from rest_framework.serializers import ModelSerializer
from rest_framework.serializers import raise_errors_on_nested_writes
from rest_framework.serializers import Serializer
from rest_framework.serializers import ValidationError
from rest_framework.settings import api_settings
from rest_framework.status import HTTP_201_CREATED
from rest_framework.utils import html
from rest_framework.utils import model_meta
from rest_framework.viewsets import ReadOnlyModelViewSet

from contentcuration.viewsets.common import MissingRequiredParamsException


_valid_positions = {"first-child", "last-child", "left", "right"}


class SimpleReprMixin(object):
    def __repr__(self):
        """
        DRF's default __repr__ implementation prints out all fields, and in the process
        of that can evaluate querysets. If those querysets haven't yet had filters applied,
        this will lead to full table scans, which are a big no-no if you like running servers.
        """
        return "{} object".format(self.__class__.__name__)


# Add mixin first to make sure __repr__ for mixin is first in MRO
class BulkModelSerializer(SimpleReprMixin, ModelSerializer):
    def __init__(self, *args, **kwargs):
        super(BulkModelSerializer, self).__init__(*args, **kwargs)
        # Track any changes that should be propagated back to the frontend
        self.changes = []

    @classmethod
    def id_attr(cls):
        ModelClass = cls.Meta.model
        info = model_meta.get_field_info(ModelClass)
        return getattr(cls.Meta, "update_lookup_field", info.pk.name)

    def get_value(self, data, attr):
        """
        Method to get a value based on the attribute name
        accepts data which can be either a dict or a Django Model
        Uses the underlying DRF Field methods for the field
        to return the value.
        """
        id_field = self.fields[attr]
        if isinstance(data, dict):
            return id_field.get_value(data)
        else:
            # Otherwise should be a model instance
            return id_field.get_attribute(data)

    def id_value_lookup(self, data):
        """
        Method to get the value for an id to use in lookup dicts
        In the case of a simple id, this is just the str of the value
        In the case of a combined index, we make a stringified array
        representation of the values.
        """
        id_attr = self.id_attr()

        if isinstance(id_attr, str):
            return str(self.get_value(data, id_attr))
        else:
            # Could alternatively have coerced the list of values to a string
            # but this seemed more explicit in terms of the intended format.
            return "[{}]".format(
                ",".join((str(self.get_value(data, attr)) for attr in id_attr))
            )

    def set_id_values(self, data, obj):
        """
        Method to set all ids values on a dict (obj)
        from either a dict or a model (data)
        """
        obj.update(self.get_id_values(data))
        return obj

    def get_id_values(self, data):
        """
        Return a dict of the id value(s) from data
        which can be either a dict or a model
        """
        id_attr = self.id_attr()

        obj = {}

        if isinstance(id_attr, str):
            obj[id_attr] = self.get_value(data, id_attr)
        else:
            for attr in id_attr:
                obj[attr] = self.get_value(data, attr)
        return obj

    def remove_id_values(self, obj):
        """
        Remove the id value(s) from obj
        Return obj for consistency, even though this method has side
        effects.
        """
        id_attr = self.id_attr()

        if isinstance(id_attr, str):
            del obj[id_attr]
        else:
            for attr in id_attr:
                del obj[attr]
        return obj

    def to_internal_value(self, data):
        ret = super(BulkModelSerializer, self).to_internal_value(data)

        # add update_lookup_field field back to validated data
        # since super by default strips out read-only fields
        # hence id will no longer be present in validated_data
        if isinstance(self.parent, BulkListSerializer):
            self.set_id_values(data, ret)

        return ret

    def update(self, instance, validated_data):
        # To ensure caution, require nested_writes to be explicitly allowed
        if not (hasattr(self.Meta, "nested_writes") and self.Meta.nested_writes):
            raise_errors_on_nested_writes("update", self, validated_data)
        info = model_meta.get_field_info(instance)

        # Simply set each attribute on the instance, and then save it.
        # Note that unlike `.create()` we don't need to treat many-to-many
        # relationships as being a special case. During updates we already
        # have an instance pk for the relationships to be associated with.
        for attr, value in validated_data.items():
            if attr in info.relations and info.relations[attr].to_many:
                raise ValueError("Many to many fields must be explicitly handled", attr)
            else:
                setattr(instance, attr, value)

        if hasattr(instance, "on_update") and callable(instance.on_update):
            instance.on_update()

        if not getattr(self, "parent"):
            instance.save()

        return instance

    def create(self, validated_data):
        # To ensure caution, require nested_writes to be explicitly allowed
        if not (hasattr(self.Meta, "nested_writes") and self.Meta.nested_writes):
            raise_errors_on_nested_writes("create", self, validated_data)

        ModelClass = self.Meta.model

        # Remove many-to-many relationships from validated_data.
        # They are not valid arguments to the default `.create()` method,
        # as they require that the instance has already been saved.
        info = model_meta.get_field_info(ModelClass)
        for field_name, relation_info in info.relations.items():
            if relation_info.to_many and (field_name in validated_data):
                raise ValueError(
                    "Many to many fields must be explicitly handled", field_name
                )
            elif not relation_info.reverse and (field_name in validated_data):
                if not isinstance(
                    validated_data[field_name], relation_info.related_model
                ):
                    # Trying to set a foreign key but do not have the object, only the key
                    validated_data[
                        relation_info.model_field.attname
                    ] = validated_data.pop(field_name)

        instance = ModelClass(**validated_data)

        if hasattr(instance, "on_create") and callable(instance.on_create):
            instance.on_create()

        if not getattr(self, "parent", False):
            instance.save()

        return instance


# Add mixin first to make sure __repr__ for mixin is first in MRO
class BulkListSerializer(SimpleReprMixin, ListSerializer):
    def __init__(self, *args, **kwargs):
        super(BulkListSerializer, self).__init__(*args, **kwargs)
        # Track any changes that should be propagated back to the frontend
        self.changes = []
        # Track any objects that weren't found
        self.missing_keys = set()

    def _data_lookup_dict(self):
        """
        Return a data lookup dict keyed by the id attribute
        based off the Django in bulk method
        """
        if self.instance:
            return {self.child.id_value_lookup(obj): obj for obj in self.instance}
        return {}

    def to_internal_value(self, data):
        """
        List of dicts of native values <- List of dicts of primitive datatypes.
        Modified from https://github.com/encode/django-rest-framework/blob/master/rest_framework/serializers.py
        based on suggestions from https://github.com/miki725/django-rest-framework-bulk/issues/68
        This is to prevent an error whereby the DRF Unique validator fails when the instance on the child
        serializer is a queryset and not an object.
        """
        if html.is_html_input(data):
            data = html.parse_html_list(data, default=[])

        if not isinstance(data, list):
            message = self.error_messages["not_a_list"].format(
                input_type=type(data).__name__
            )
            raise ValidationError(
                {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="not_a_list"
            )

        if not self.allow_empty and len(data) == 0:
            message = self.error_messages["empty"]
            raise ValidationError(
                {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="empty"
            )

        ret = []
        errors = []

        data_lookup = self._data_lookup_dict()

        for item in data:
            try:
                # prepare child serializer to only handle one instance
                self.child.instance = data_lookup.get(self.child.id_value_lookup(item))
                self.child.initial_data = item
                validated = self.child.run_validation(item)
            except ValidationError as exc:
                errors.append(exc.detail)
            else:
                ret.append(validated)
                errors.append({})

        if any(errors):
            raise ValidationError(errors)

        return ret

    def update(self, queryset, all_validated_data):
        concrete_fields = set(
            f.name for f in self.child.Meta.model._meta.concrete_fields
        )

        all_validated_data_by_id = {}

        properties_to_update = set()

        for obj in all_validated_data:
            obj_id = self.child.id_value_lookup(obj)
            obj = self.child.remove_id_values(obj)
            if obj.keys():
                all_validated_data_by_id[obj_id] = obj
                properties_to_update.update(obj.keys())

        properties_to_update = properties_to_update.intersection(concrete_fields)

        # this method is handed a queryset that has been pre-filtered
        # to the specific instance ids in question, by `create_from_updates` on the bulk update mixin
        objects_to_update = queryset.only(*properties_to_update)

        updated_objects = []

        updated_keys = set()

        for obj in objects_to_update:
            # Coerce to string as some ids are of the UUID class
            obj_id = self.child.id_value_lookup(obj)
            obj_validated_data = all_validated_data_by_id.get(obj_id)

            # If no valid data was passed back then this will be None
            if obj_validated_data is not None:

                # Reset the child serializer changes attribute
                self.child.changes = []
                # use model serializer to actually update the model
                # in case that method is overwritten

                instance = self.child.update(obj, obj_validated_data)
                # If the update method does not return an instance for some reason
                # do not try to run further updates on the model, as there is no
                # object to udpate.
                if instance:
                    updated_objects.append(instance)
                # Collect any registered changes from this run of the loop
                self.changes.extend(self.child.changes)

                updated_keys.add(obj_id)

        if len(all_validated_data_by_id) != len(updated_keys):
            self.missing_keys = updated_keys.difference(
                set(all_validated_data_by_id.keys())
            )

        bulk_update(objects_to_update, update_fields=properties_to_update)

        return updated_objects

    def create(self, validated_data):
        ModelClass = self.child.Meta.model
        objects_to_create = []
        for model_data in validated_data:
            # Reset the child serializer changes attribute
            self.child.changes = []
            object_to_create = self.child.create(model_data)
            objects_to_create.append(object_to_create)
            # Collect any registered changes from this run of the loop
            self.changes.extend(self.child.changes)
        try:
            created_objects = ModelClass._default_manager.bulk_create(objects_to_create)
        except TypeError:
            tb = traceback.format_exc()
            msg = (
                "Got a `TypeError` when calling `%s.%s.create()`. "
                "This may be because you have a writable field on the "
                "serializer class that is not a valid argument to "
                "`%s.%s.create()`. You may need to make the field "
                "read-only, or override the %s.create() method to handle "
                "this correctly.\nOriginal exception was:\n %s"
                % (
                    ModelClass.__name__,
                    ModelClass._default_manager.name,
                    ModelClass.__name__,
                    ModelClass._default_manager.name,
                    self.__class__.__name__,
                    tb,
                )
            )
            raise TypeError(msg)
        return created_objects


class ReadOnlyValuesViewset(SimpleReprMixin, ReadOnlyModelViewSet):
    """
    A viewset that uses a values call to get all model/queryset data in
    a single database query, rather than delegating serialization to a
    DRF ModelSerializer.
    """

    # A tuple of values to get from the queryset
    values = None
    # A map of target_key, source_key where target_key is the final target_key that will be set
    # and source_key is the key on the object retrieved from the values call.
    # Alternatively, the source_key can be a callable that will be passed the object and return
    # the value for the target_key. This callable can also pop unwanted values from the obj
    # to remove unneeded keys from the object as a side effect.
    field_map = {}

    def __init__(self, *args, **kwargs):
        viewset = super(ReadOnlyValuesViewset, self).__init__(*args, **kwargs)
        if not isinstance(self.values, tuple):
            raise TypeError("values must be defined as a tuple")
        self._values = tuple(self.values)
        if not isinstance(self.field_map, dict):
            raise TypeError("field_map must be defined as a dict")
        self._field_map = self.field_map.copy()
        return viewset

    @classmethod
    def id_attr(cls):
        if cls.serializer_class is not None and hasattr(
            cls.serializer_class, "id_attr"
        ):
            return cls.serializer_class.id_attr()
        return None

    @classmethod
    def values_from_key(cls, key):
        """
        Method to return an iterable that can be used as arguments for dict
        to return the values from key.
        Key is either a string, in which case the key is a singular value
        or a list, in which case the key is a combined value.
        """
        id_attr = cls.id_attr()

        if id_attr:
            if isinstance(id_attr, str):
                # Singular value
                # Just return the single id_attr and the original key
                return [(id_attr, key)]
            else:
                # Multiple values in the key, zip together the id_attr and the key
                # to create key, value pairs for a dict
                # Order in the key matters, and must match the "update_lookup_field"
                # property of the serializer.
                return [(attr, value) for attr, value in zip(id_attr, key)]
        return []

    @classmethod
    def filter_queryset_from_keys(cls, queryset, keys):
        """
        Method to filter a queryset based on keys.
        """
        id_attr = cls.id_attr()

        if id_attr:
            if isinstance(id_attr, str):
                # In the case of single valued keys, this is just an __in lookup
                return queryset.filter(**{"{}__in".format(id_attr): keys})
            else:
                # If id_attr is multivalued we need to do an ORed lookup for each
                # set of values represented by a key.
                # This is probably not as performant as the simple __in query
                # improvements welcome!
                query = Q()
                for key in keys:
                    query |= Q(**{attr: value for attr, value in zip(id_attr, key)})
                return queryset.filter(query)
        return queryset.none()

    def get_serializer_class(self):
        if self.serializer_class is not None:
            return self.serializer_class
        # Hack to prevent the renderer logic from breaking completely.
        return Serializer

    def get_queryset(self):
        queryset = super(ReadOnlyValuesViewset, self).get_queryset()
        if self.request.user.is_admin:
            return queryset
        if hasattr(queryset.model, "filter_view_queryset"):
            return queryset.model.filter_view_queryset(queryset, self.request.user)
        return queryset

    def get_edit_queryset(self):
        """
        Return a filtered copy of the queryset to only the objects
        that a user is able to edit, rather than view.
        """
        queryset = super(ReadOnlyValuesViewset, self).get_queryset()
        if self.request.user.is_admin:
            return queryset
        if hasattr(queryset.model, "filter_edit_queryset"):
            return queryset.model.filter_edit_queryset(queryset, self.request.user)
        return self.get_queryset()

    def _get_object_from_queryset(self, queryset):
        """
        Returns the object the view is displaying.
        We override this to remove the DRF default behaviour
        of filtering the queryset.
        (rtibbles) There doesn't seem to be a use case for
        querying a detail endpoint and also filtering by query
        parameters that might result in a 404.
        """
        # Perform the lookup filtering.
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (
            "Expected view %s to be called with a URL keyword argument "
            'named "%s". Fix your URL conf, or set the `.lookup_field` '
            "attribute on the view correctly."
            % (self.__class__.__name__, lookup_url_kwarg)
        )

        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        obj = get_object_or_404(queryset, **filter_kwargs)

        # May raise a permission denied
        self.check_object_permissions(self.request, obj)

        return obj

    def get_object(self):
        return self._get_object_from_queryset(self.get_queryset())

    def get_edit_object(self):
        return self._get_object_from_queryset(self.get_edit_queryset())

    def annotate_queryset(self, queryset):
        return queryset

    def prefetch_queryset(self, queryset):
        return queryset

    def _map_fields(self, item):
        for key, value in self._field_map.items():
            if callable(value):
                item[key] = value(item)
            elif value in item:
                item[key] = item.pop(value)
            else:
                item[key] = value
        return item

    def consolidate(self, items, queryset):
        return items

    def _cast_queryset_to_values(self, queryset):
        queryset = self.annotate_queryset(queryset)
        return queryset.values(*self._values)

    def serialize(self, queryset):
        return self.consolidate(list(map(self._map_fields, queryset or [])), queryset)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.prefetch_queryset(self.get_queryset()))
        queryset = self._cast_queryset_to_values(queryset)

        page = self.paginate_queryset(queryset)

        if page is not None:
            return self.get_paginated_response(self.serialize(page))

        return Response(self.serialize(queryset))

    def serialize_object(self, pk):
        queryset = self.prefetch_queryset(self.get_queryset())
        try:
            return self.serialize(
                self._cast_queryset_to_values(queryset.filter(pk=pk))
            )[0]
        except IndexError:
            raise Http404(
                "No %s matches the given query." % queryset.model._meta.object_name
            )

    def retrieve(self, request, pk, *args, **kwargs):
        return Response(self.serialize_object(pk))


class ValuesViewset(ReadOnlyValuesViewset, DestroyModelMixin):
    def _map_create_change(self, change):
        return dict(
            [(k, v) for k, v in change["obj"].items()]
            + self.values_from_key(change["key"])
        )

    def _map_update_change(self, change):
        return dict(
            [(k, v) for k, v in change["mods"].items()]
            + self.values_from_key(change["key"])
        )

    def _map_delete_change(self, change):
        return change["key"]

    def perform_create(self, serializer):
        serializer.save()

    def create_from_changes(self, changes):
        errors = []
        changes_to_return = []

        for change in changes:
            serializer = self.get_serializer(data=self._map_create_change(change))
            if serializer.is_valid():
                self.perform_create(serializer)
                if serializer.changes:
                    changes_to_return.extend(serializer.changes)
            else:
                change.update({"errors": serializer.errors})
                errors.append(change)

        return errors, changes_to_return

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        return Response(self.serialize_object(instance.id), status=HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save()

    def update_from_changes(self, changes):
        errors = []
        changes_to_return = []
        queryset = self.get_edit_queryset().order_by()
        for change in changes:
            try:
                instance = queryset.get(**dict(self.values_from_key(change["key"])))
                serializer = self.get_serializer(
                    instance, data=self._map_update_change(change), partial=True
                )
                if serializer.is_valid():
                    self.perform_update(serializer)
                    if serializer.changes:
                        changes_to_return.extend(serializer.changes)
                else:
                    change.update({"errors": serializer.errors})
                    errors.append(change)
            except ObjectDoesNotExist:
                # Should we also check object permissions here and return a different
                # error if the user can view the object but not edit it?
                change.update({"errors": ValidationError("Not found").detail})
                errors.append(change)
        return errors, changes_to_return

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_edit_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(self.serialize_object(instance.id))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def delete_from_changes(self, changes):
        errors = []
        changes_to_return = []
        queryset = self.get_edit_queryset().order_by()
        for change in changes:
            try:
                instance = queryset.get(**dict(self.values_from_key(change["key"])))

                instance.delete()
            except ObjectDoesNotExist:
                # Should we also check object permissions here and return a different
                # error if the user can view the object but not edit it?
                change.update({"errors": ValidationError("Not found").detail})
                errors.append(change)
        return errors, changes_to_return


class BulkCreateMixin(object):
    def perform_bulk_create(self, serializer):
        serializer.save()

    def create_from_changes(self, changes):
        data = list(map(self._map_create_change, changes))
        serializer = self.get_serializer(data=data, many=True)
        errors = []
        if serializer.is_valid():
            self.perform_bulk_create(serializer)
        else:
            valid_data = []
            for error, datum in zip(serializer.errors, data):
                if error:
                    datum.update({"errors": error})
                    errors.append(datum)
                else:
                    valid_data.append(datum)
            if valid_data:
                serializer = self.get_serializer(data=valid_data, many=True)
                # This should now not raise an exception as we have filtered
                # all the invalid objects, but we still need to call is_valid
                # before DRF will let us save them.
                serializer.is_valid(raise_exception=True)
                self.perform_bulk_create(serializer)
        return errors, serializer.changes


class BulkUpdateMixin(object):
    def perform_bulk_update(self, serializer):
        serializer.save()

    def update_from_changes(self, changes):
        data = list(map(self._map_update_change, changes))
        keys = [change["key"] for change in changes]
        queryset = self.filter_queryset_from_keys(
            self.get_edit_queryset(), keys
        ).order_by()
        serializer = self.get_serializer(queryset, data=data, many=True, partial=True)
        errors = []

        if serializer.is_valid():
            self.perform_bulk_update(serializer)
            if serializer.missing_keys:
                # add errors for any changes that were specified but no object
                # corresponding could be found
                errors = [
                    dict(error=ValidationError("Not found").detail, **change)
                    for change in changes
                    if change["key"] in serializer.missing_keys
                ]
        else:
            valid_data = []
            for error, datum in zip(serializer.errors, changes):
                if error:
                    # If the user does not have permission to write to this object
                    # it will throw a uniqueness validation error when trying to
                    # validate the id attribute for the change
                    # intercept this and replace with not found.

                    if self.id_attr() in error and any(
                        map(
                            lambda x: getattr(x, "code", None) == "unique",
                            error[self.id_attr()],
                        )
                    ):
                        error = ValidationError("Not found").detail
                    datum.update({"errors": error})
                    errors.append(datum)
                else:
                    valid_data.append(datum)
            if valid_data:
                serializer = self.get_serializer(
                    queryset, data=valid_data, many=True, partial=True
                )
                # This should now not raise an exception as we have filtered
                # all the invalid objects, but we still need to call is_valid
                # before DRF will let us save them.
                serializer.is_valid(raise_exception=True)
                self.perform_bulk_update(serializer)
        return errors, serializer.changes


class BulkDeleteMixin(object):
    def delete_from_changes(self, changes):
        keys = [change["key"] for change in changes]
        queryset = self.filter_queryset_from_keys(
            self.get_edit_queryset(), keys
        ).order_by()
        errors = []
        changes_to_return = []
        try:
            queryset.delete()
        except Exception:
            errors = [
                {
                    "key": not_deleted_id,
                    "errors": ValidationError("Could not be deleted").detail,
                }
                for not_deleted_id in keys
            ]
        return errors, changes_to_return


class CopyMixin(object):
    def copy_from_changes(self, changes):
        errors = []
        changes_to_return = []
        for copy in changes:
            # Copy change will have key, must also have other attributes, defined in `copy`
            copy_errors, copy_changes = self.copy(
                copy["key"], from_key=copy["from_key"], **copy["mods"]
            )
            if copy_errors:
                copy.update({"errors": copy_errors})
                errors.append(copy)
            if copy_changes:
                changes_to_return.extend(copy_changes)
        return errors, changes_to_return


class MoveMixin(object):
    def validate_targeting_args(self, target, position):
        if target is None:
            raise ValidationError("A target must be specified")
        try:
            target = self.get_edit_queryset().get(pk=target)
        except ObjectDoesNotExist:
            raise ValidationError("Target: {} does not exist".format(target))
        except ValueError:
            raise ValidationError("Invalid target specified: {}".format(target))
        if position not in _valid_positions:
            raise ValidationError(
                "Invalid position specified, must be one of {}".format(
                    ", ".join(_valid_positions)
                )
            )
        return target, position

    def move_from_changes(self, changes):
        errors = []
        changes_to_return = []
        for move in changes:
            # Move change will have key, must also have target property
            # optionally can include the desired position.
            target = move["mods"].get("target")
            position = move["mods"].get("position")
            move_error, move_change = self.move(
                move["key"], target=target, position=position
            )
            if move_error:
                move.update({"errors": [move_error]})
                errors.append(move)
            if move_change:
                changes_to_return.append(move_change)
        return errors, changes_to_return


class RequiredFilterSet(FilterSet):
    # @property
    # def qs(self):
    #     has_filtering_queries = False
    #     if self.form.is_valid():
    #         for name, filter_ in self.filters.items():
    #             value = self.form.cleaned_data.get(name)

    #             if value not in EMPTY_VALUES:
    #                 has_filtering_queries = True
    #                 break
    #     if not has_filtering_queries:
    #         raise MissingRequiredParamsException("No valid filter parameters supplied")
    #     return super(FilterSet, self).qs
    pass
