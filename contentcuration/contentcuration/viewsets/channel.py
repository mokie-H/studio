from django.conf import settings
from django.core.cache import cache
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django_filters.rest_framework import BooleanFilter
from django_filters.rest_framework import CharFilter
from django_filters.rest_framework import DjangoFilterBackend
from le_utils.constants import content_kinds
from le_utils.constants import roles
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.permissions import IsAdminUser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from contentcuration.decorators import cache_no_user_data
from contentcuration.models import Channel
from contentcuration.models import ContentNode
from contentcuration.models import generate_storage_url
from contentcuration.models import SecretToken
from contentcuration.models import User
from contentcuration.tasks import cache_multiple_channels_metadata_task
from contentcuration.utils.cache import DEFERRED_FLAG
from contentcuration.utils.channel import CACHE_CHANNEL_KEY
from contentcuration.viewsets.base import BulkListSerializer
from contentcuration.viewsets.base import BulkModelSerializer
from contentcuration.viewsets.base import ReadOnlyValuesViewset
from contentcuration.viewsets.base import RequiredFilterSet
from contentcuration.viewsets.base import ValuesViewset
from contentcuration.viewsets.common import CatalogPaginator
from contentcuration.viewsets.common import ContentDefaultsSerializer
from contentcuration.viewsets.common import SQCount
from contentcuration.viewsets.common import UUIDInFilter
from contentcuration.viewsets.sync.constants import CHANNEL
from contentcuration.viewsets.sync.utils import generate_update_event


class CatalogListPagination(PageNumberPagination):
    page_size = None
    page_size_query_param = "page_size"
    max_page_size = 1000
    django_paginator_class = CatalogPaginator

    def get_paginated_response(self, data):
        return Response(
            {
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "page_number": self.page.number,
                "count": self.page.paginator.count,
                "total_pages": self.page.paginator.num_pages,
                "results": data,
            }
        )


primary_token_subquery = Subquery(
    SecretToken.objects.filter(channels=OuterRef("id"), is_primary=True)
    .values("token")
    .order_by("-token")[:1]
)


base_channel_filter_fields = (
    "keywords",
    "published",
    "languages",
    "licenses",
    "kinds",
    "coach",
    "assessments",
    "subtitles",
    "public",
    "id__in",
    "collection",
    "deleted",
    "staged",
    "cheffed",
)


class BaseChannelFilter(RequiredFilterSet):
    published = BooleanFilter(name="main_tree__published")
    id__in = UUIDInFilter(name="id")
    keywords = CharFilter(method="filter_keywords")
    languages = CharFilter(method="filter_languages")
    licenses = CharFilter(method="filter_licenses")
    kinds = CharFilter(method="filter_kinds")
    coach = BooleanFilter(method="filter_coach")
    assessments = BooleanFilter(method="filter_assessments")
    subtitles = BooleanFilter(method="filter_subtitles")
    collection = CharFilter(method="filter_collection")
    deleted = BooleanFilter(method="filter_deleted")
    staged = BooleanFilter(method="filter_staged")
    public = BooleanFilter(method="filter_public")
    cheffed = BooleanFilter(method="filter_cheffed")
    exclude = CharFilter(name="id", method="filter_excluded_id")

    def __init__(self, *args, **kwargs):
        super(BaseChannelFilter, self).__init__(*args, **kwargs)
        self.main_tree_query = ContentNode.objects.filter(
            tree_id=OuterRef("main_tree__tree_id")
        )

    def filter_deleted(self, queryset, name, value):
        return queryset.filter(deleted=value)

    def filter_keywords(self, queryset, name, value):
        # TODO: Wait until we show more metadata on cards to add this back in
        # keywords_query = self.main_tree_query.filter(
        #     Q(tags__tag_name__icontains=value)
        #     | Q(author__icontains=value)
        #     | Q(aggregator__icontains=value)
        #     | Q(provider__icontains=value)
        # )
        return queryset.annotate(
            # keyword_match_count=SQCount(keywords_query, field="content_id"),
            primary_token=primary_token_subquery,
        ).filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
            | Q(pk__istartswith=value)
            | Q(primary_token=value.replace("-", ""))
            # | Q(keyword_match_count__gt=0)
        )

    def filter_languages(self, queryset, name, value):
        languages = value.split(",")

        # TODO: Wait until we show more metadata on cards to add this back in
        # language_query = (
        #     self.main_tree_query.filter(language_id__in=languages)
        #     .values("content_id")
        #     .distinct()
        # )
        # return queryset.annotate(
        #     language_count=SQCount(language_query, field="content_id")
        # ).filter(Q(language_id__in=languages) | Q(language_count__gt=0))

        return queryset.filter(language__lang_code__in=languages)

    def filter_licenses(self, queryset, name, value):
        license_query = (
            self.main_tree_query.filter(
                license_id__in=[int(l_id) for l_id in value.split(",")]
            )
            .values("content_id")
            .distinct()
        )
        return queryset.annotate(
            license_count=SQCount(license_query, field="content_id")
        ).exclude(license_count=0)

    def filter_kinds(self, queryset, name, value):
        kinds_query = (
            self.main_tree_query.filter(kind_id__in=value.split(","))
            .values("content_id")
            .distinct()
        )
        return queryset.annotate(
            kind_match_count=SQCount(kinds_query, field="content_id")
        ).exclude(kind_match_count=0)

    def filter_coach(self, queryset, name, value):
        coach_query = self.main_tree_query.filter(role_visibility=roles.COACH)
        return queryset.annotate(
            coach_count=SQCount(coach_query, field="content_id")
        ).exclude(coach_count=0)

    def filter_assessments(self, queryset, name, value):
        assessment_query = self.main_tree_query.filter(kind_id=content_kinds.EXERCISE)
        return queryset.annotate(
            assessment_count=SQCount(assessment_query, field="content_id")
        ).exclude(assessment_count=0)

    def filter_subtitles(self, queryset, name, value):
        subtitle_query = self.main_tree_query.filter(files__preset__subtitle=True)
        return queryset.annotate(
            subtitle_count=SQCount(subtitle_query, field="content_id")
        ).exclude(subtitle_count=0)

    def filter_collection(self, queryset, name, value):
        return queryset.filter(secret_tokens__channel_sets__pk=value)

    def filter_staged(self, queryset, name, value):
        return queryset.exclude(staging_tree=None)

    def filter_public(self, queryset, name, value):
        return queryset.filter(public=value)

    def filter_cheffed(self, queryset, name, value):
        return queryset.exclude(ricecooker_version=None)

    def filter_excluded_id(self, queryset, name, value):
        return queryset.exclude(pk=value)

    class Meta:
        model = Channel
        fields = base_channel_filter_fields


class ChannelFilter(BaseChannelFilter):
    edit = BooleanFilter(method="filter_edit")
    view = BooleanFilter(method="filter_view")
    bookmark = BooleanFilter(method="filter_bookmark")

    def filter_edit(self, queryset, name, value):
        return queryset.filter(edit=True)

    def filter_view(self, queryset, name, value):
        return queryset.filter(view=True)

    def filter_bookmark(self, queryset, name, value):
        return queryset.filter(bookmark=True)

    class Meta:
        model = Channel
        fields = base_channel_filter_fields + ("bookmark", "edit", "view",)


class ChannelSerializer(BulkModelSerializer):
    """
    This is a write only serializer - we leverage it to do create and update
    operations, but read operations are handled by the Viewset.
    """

    bookmark = serializers.BooleanField(required=False)
    content_defaults = ContentDefaultsSerializer(partial=True, required=False)

    class Meta:
        model = Channel
        fields = (
            "id",
            "deleted",
            "name",
            "description",
            "thumbnail",
            "thumbnail_encoding",
            "version",
            "language",
            "bookmark",
            "content_defaults",
            "source_domain",
            "source_url",
            "demo_server_url",
            "grade",
            "reference",
            "course_field"
        )
        read_only_fields = ("version",)
        list_serializer_class = BulkListSerializer
        nested_writes = True

    def create(self, validated_data):
        bookmark = validated_data.pop("bookmark", None)
        content_defaults = validated_data.pop("content_defaults", {})
        validated_data["content_defaults"] = self.fields["content_defaults"].create(
            content_defaults
        )
        instance = super(ChannelSerializer, self).create(validated_data)
        if "request" in self.context:
            user = self.context["request"].user
            # This has been newly created so add the current user as an editor
            instance.editors.add(user)
            if bookmark:
                user.bookmarked_channels.add(instance)
        self.changes.append(
            generate_update_event(
                instance.id,
                CHANNEL,
                {
                    "root_id": instance.main_tree.id,
                    "created": instance.main_tree.created,
                    "published": instance.main_tree.published,
                    "content_defaults": instance.content_defaults,
                },
            )
        )
        return instance

    def update(self, instance, validated_data):
        bookmark = validated_data.pop("bookmark", None)
        content_defaults = validated_data.pop("content_defaults", None)
        if content_defaults is not None:
            validated_data["content_defaults"] = self.fields["content_defaults"].update(
                instance.content_defaults, content_defaults
            )

        if "request" in self.context:
            user_id = self.context["request"].user.id
            # We could possibly do this in bulk later in the process,
            # but bulk creating many to many through table models
            # would be required, and that would need us to be able to
            # efficiently ignore conflicts with existing models.
            # When we have upgraded to Django 2.2, we can do the bulk
            # creation of many to many models to make this more efficient
            # and use the `ignore_conflicts=True` kwarg to ignore
            # any conflicts.
            if bookmark is not None and bookmark:
                instance.bookmarked_by.add(user_id)
            elif bookmark is not None:
                instance.bookmarked_by.remove(user_id)

        return super(ChannelSerializer, self).update(instance, validated_data)


def get_thumbnail_url(item):
    return item.get("thumbnail") and generate_storage_url(item["thumbnail"])


def _format_url(url):
    if not url:
        return ""
    elif url.startswith("http"):
        return url
    else:
        return "//{}".format(url)


def format_source_url(item):
    return _format_url(item.get("source_url"))


def format_demo_server_url(item):
    return _format_url(item.get("demo_server_url"))


base_channel_values = (
    "id",
    "name",
    "description",
    "main_tree__published",
    "thumbnail",
    "thumbnail_encoding",
    "language",
    "primary_token",
    "modified",
    "count",
    "public",
    "version",
    "main_tree__created",
    "last_published",
    "ricecooker_version",
    "main_tree__id",
    "content_defaults",
    "deleted",
    "trash_tree__id",
    "staging_tree__id",
    "source_url",
    "demo_server_url",
    "grade",
    "reference",
    "course_field"
)

channel_field_map = {
    "thumbnail_url": get_thumbnail_url,
    "published": "main_tree__published",
    "created": "main_tree__created",
    "root_id": "main_tree__id",
    "trash_root_id": "trash_tree__id",
    "staging_root_id": "staging_tree__id",
    "source_url": format_source_url,
    "demo_server_url": format_demo_server_url,
}


class ChannelViewSet(ValuesViewset):
    queryset = Channel.objects.all()
    permission_classes = [IsAuthenticated]
    serializer_class = ChannelSerializer
    filter_backends = (DjangoFilterBackend,)
    pagination_class = CatalogListPagination
    filter_class = ChannelFilter

    field_map = channel_field_map
    values = base_channel_values + ("edit", "view", "bookmark")

    def get_queryset(self):
        queryset = super(ChannelViewSet, self).get_queryset()
        user_id = not self.request.user.is_anonymous() and self.request.user.id
        user_queryset = User.objects.filter(id=user_id)

        return queryset.annotate(
            edit=Exists(user_queryset.filter(editable_channels=OuterRef("id"))),
            view=Exists(user_queryset.filter(view_only_channels=OuterRef("id"))),
            bookmark=Exists(user_queryset.filter(bookmarked_channels=OuterRef("id"))),
        )

    def annotate_queryset(self, queryset):
        queryset = queryset.annotate(primary_token=primary_token_subquery)
        channel_main_tree_nodes = ContentNode.objects.filter(
            tree_id=OuterRef("main_tree__tree_id")
        )
        # Add the last modified node modified value as the channel last modified
        queryset = queryset.annotate(
            modified=Subquery(
                channel_main_tree_nodes.values("modified").order_by("-modified")[:1]
            )
        )
        # Add the unique count of distinct non-topic node content_ids
        non_topic_content_ids = (
            channel_main_tree_nodes.exclude(kind_id=content_kinds.TOPIC)
            .values_list("content_id", flat=True)
            .order_by()
            .distinct()
        )

        queryset = queryset.annotate(
            count=SQCount(non_topic_content_ids, field="content_id"),
        )
        return queryset


@method_decorator(
    cache_page(
        settings.PUBLIC_CHANNELS_CACHE_DURATION, key_prefix="public_catalog_list"
    ),
    name="dispatch",
)
@method_decorator(cache_no_user_data, name="dispatch")
class CatalogViewSet(ReadOnlyValuesViewset):
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    filter_backends = (DjangoFilterBackend,)
    pagination_class = CatalogListPagination
    filter_class = BaseChannelFilter

    permission_classes = [AllowAny]

    field_map = channel_field_map
    values = base_channel_values

    def get_queryset(self):
        queryset = Channel.objects.filter(deleted=False, public=True)

        return queryset.order_by("name")

    def annotate_queryset(self, queryset):
        queryset = queryset.annotate(primary_token=primary_token_subquery)
        channel_main_tree_nodes = ContentNode.objects.filter(
            tree_id=OuterRef("main_tree__tree_id")
        )
        # Add the last modified node modified value as the channel last modified
        queryset = queryset.annotate(
            modified=Subquery(
                channel_main_tree_nodes.values("modified").order_by("-modified")[:1]
            )
        )
        # Add the unique count of distinct non-topic node content_ids
        non_topic_content_ids = (
            channel_main_tree_nodes.exclude(kind_id=content_kinds.TOPIC)
            .order_by("content_id")
            .distinct("content_id")
            .values_list("content_id", flat=True)
        )

        queryset = queryset.annotate(
            count=SQCount(non_topic_content_ids, field="content_id"),
        )
        return queryset


class AdminChannelFilter(BaseChannelFilter):
    def filter_keywords(self, queryset, name, value):
        regex = r"^(" + "|".join(value.split(" ")) + ")$"
        return queryset.annotate(primary_token=primary_token_subquery,).filter(
            Q(name__icontains=value)
            | Q(pk__istartswith=value)
            | Q(primary_token=value.replace("-", ""))
            | (
                Q(editors__first_name__iregex=regex)
                & Q(editors__last_name__iregex=regex)
            )
            | Q(editors__email__iregex=regex)
        )


class AdminChannelViewSet(ChannelViewSet):
    pagination_class = CatalogListPagination
    permission_classes = [IsAdminUser]
    filter_class = AdminChannelFilter
    filter_backends = (
        DjangoFilterBackend,
        OrderingFilter,
    )
    values = base_channel_values + ("main_tree__tree_id",)
    ordering_fields = (
        "name",
        "id",
        "editors_count",
        "viewers_count",
        "size",
        "modified",
        "created",
        "primary_token",
        "source_url",
        "demo_server_url",
    )
    ordering = ("name",)

    def get_queryset(self):
        return Channel.objects.all().order_by("name")

    def annotate_queryset(self, queryset):
        queryset = super(AdminChannelViewSet, self).annotate_queryset(queryset)
        return queryset

    def consolidate(self, items, queryset):
        if items:
            cache_multiple_channels_metadata_task.delay(items)
            for item_channel in items:
                metadata = self.get_or_cache_channel_metadata(
                    item_channel["id"], item_channel["main_tree__tree_id"]
                )
                if metadata == DEFERRED_FLAG:
                    item_channel["size"] = item_channel["editors_count"] = item_channel[
                        "viewers_count"
                    ] = DEFERRED_FLAG
                else:
                    item_channel.update(metadata)
        return items

    def get_or_cache_channel_metadata(self, channel_id, tree_id):
        """
        Returns cached data for the channel, if it exists
        If there's not a key for the channel in the cache
        it triggers an async task to calculate it
        """
        key = CACHE_CHANNEL_KEY.format(channel_id)
        cached_info = cache.get(key)
        # cache_channel_metadata_task.delay(key, channel_id, tree_id)
        if cached_info is None:
            # from contentcuration.utils.channel import calculate_channel_metadata
            # calculate_channel_metadata(key, channel_id, tree_id)
            return DEFERRED_FLAG
        else:
            if "METADATA" in cached_info:
                return cached_info["METADATA"]
            else:
                return DEFERRED_FLAG

    def get_metadata_for_channel(self, channel_id):
        """
        Returns cached metadata for the channel but
        does not trigger a new task to update it if
        there's nothing in the cache
        """
        key = CACHE_CHANNEL_KEY.format(channel_id)
        cached_info = cache.get(key)
        metadata = {
            "size": DEFERRED_FLAG,
            "editors_count": DEFERRED_FLAG,
            "viewers_count": DEFERRED_FLAG,
        }
        if cached_info is not None:
            if "METADATA" in cached_info:
                return cached_info["METADATA"]
        return metadata

    @action(detail=False, methods=["get"])
    def deferred_data(self, request):
        """
        Example of use:
        ///api/admin-channels/deferred_data?id__in=0598c68f00fc562486fc6616b63f267f,c1f2b7e6ac9f56a2bb44fa7a48b66dce
        """
        ids = request.GET.get("id__in")
        if not ids:
            raise ValidationError("id__in GET parameter is required")
        ids = ids.split(",")
        output = []
        for channel_id in ids:
            result = {"id": channel_id}
            result.update(self.get_metadata_for_channel(channel_id))
            output.append(result)

        return Response(output)
