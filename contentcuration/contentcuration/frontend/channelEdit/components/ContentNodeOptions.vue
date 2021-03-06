<template>

  <VList>
    <VListTile v-if="isTopic && canEdit" @click="newTopicNode">
      <VListTileTitle>{{ $tr('newSubtopic') }}</VListTileTitle>
    </VListTile>
    <VListTile v-if="canEdit && !hideEditLink" :to="editLink">
      <VListTileTitle>
        {{ isTopic? $tr('editTopicDetails') : $tr('editDetails') }}
      </VListTileTitle>
    </VListTile>
    <VListTile v-if="!hideDetailsLink" :to="viewLink">
      <VListTileTitle>{{ $tr('viewDetails') }}</VListTileTitle>
    </VListTile>
    <VListTile v-if="canEdit" @click.stop="setMoveNodes([nodeId])">
      <VListTileTitle>{{ $tr('move') }}</VListTileTitle>
    </VListTile>
    <VListTile v-if="canEdit" @click="duplicateNode()">
      <VListTileTitle>{{ $tr('makeACopy') }}</VListTileTitle>
    </VListTile>
    <VListTile @click="copyToClipboard()">
      <VListTileTitle>{{ $tr('copyToClipboard') }}</VListTileTitle>
    </VListTile>
    <VListTile v-if="canEdit" @click="removeNode()">
      <VListTileTitle>{{ $tr('remove') }}</VListTileTitle>
    </VListTile>
  </VList>

</template>

<script>

  import { mapActions, mapGetters, mapMutations } from 'vuex';
  import { RouterNames } from '../constants';
  import { withChangeTracker } from 'shared/data/changes';

  export default {
    name: 'ContentNodeOptions',
    props: {
      nodeId: {
        type: String,
        required: true,
      },
      hideDetailsLink: {
        type: Boolean,
        default: false,
      },
      hideEditLink: {
        type: Boolean,
        default: false,
      },
    },
    computed: {
      ...mapGetters('currentChannel', ['canEdit', 'trashId']),
      ...mapGetters('contentNode', ['getContentNode']),
      node() {
        return this.getContentNode(this.nodeId);
      },
      isTopic() {
        return this.node.kind === 'topic';
      },
      editLink() {
        return {
          name: RouterNames.CONTENTNODE_DETAILS,
          params: {
            ...this.$route.params,
            detailNodeIds: this.nodeId,
          },
        };
      },
      viewLink() {
        return {
          name: RouterNames.TREE_VIEW,
          params: {
            ...this.$route.params,
            detailNodeId: this.nodeId,
          },
        };
      },
    },
    methods: {
      ...mapActions(['showSnackbar']),
      ...mapActions('contentNode', ['createContentNode', 'moveContentNodes', 'copyContentNode']),
      ...mapActions('clipboard', ['copy']),
      ...mapMutations('contentNode', { setMoveNodes: 'SET_MOVE_NODES' }),
      newTopicNode() {
        let nodeData = {
          parent: this.nodeId,
          kind: 'topic',
          title: this.$tr('topicDefaultTitle', { title: this.node.title }),
        };
        this.createContentNode(nodeData).then(newId => {
          this.$router.push({
            name: RouterNames.ADD_TOPICS,
            params: {
              ...this.$route.params,
              detailNodeIds: newId,
            },
          });
        });
      },
      removeNode: withChangeTracker(function(changeTracker) {
        return this.moveContentNodes({ id__in: [this.nodeId], parent: this.trashId }).then(() => {
          return this.showSnackbar({
            text: this.$tr('removedItems'),
            actionText: this.$tr('undo'),
            actionCallback: () => changeTracker.revert(),
          });
        });
      }),
      copyToClipboard: withChangeTracker(function(changeTracker) {
        this.showSnackbar({
          duration: null,
          text: this.$tr('creatingClipboardCopies'),
          actionText: this.$tr('cancel'),
          actionCallback: () => changeTracker.revert(),
        });

        return this.copy({ node_id: this.node.node_id, channel_id: this.node.channel_id }).then(
          () => {
            return this.showSnackbar({
              text: this.$tr('copiedToClipboardSnackbar'),
              actionText: this.$tr('undo'),
              actionCallback: () => changeTracker.revert(),
            });
          }
        );
      }),
      duplicateNode: withChangeTracker(function(changeTracker) {
        this.showSnackbar({
          duration: null,
          text: this.$tr('creatingCopies'),
          actionText: this.$tr('cancel'),
          actionCallback: () => changeTracker.revert(),
        });
        const target = this.node.parent;
        return this.copyContentNode({ id: this.nodeId, target, deep: true }).then(() => {
          return this.showSnackbar({
            text: this.$tr('copiedSnackbar'),
            actionText: this.$tr('undo'),
            actionCallback: () => changeTracker.revert(),
          });
        });
      }),
    },

    $trs: {
      topicDefaultTitle: '{title} topic',
      newSubtopic: 'New topic',
      editTopicDetails: 'Edit topic details',
      editDetails: 'Edit details',
      viewDetails: 'View details',
      move: 'Move',
      makeACopy: 'Make a copy',
      copyToClipboard: 'Copy to clipboard',
      remove: 'Remove',

      undo: 'Undo',
      cancel: 'Cancel',
      creatingCopies: 'Copying...',
      creatingClipboardCopies: 'Copying to clipboard...',
      copiedSnackbar: 'Copy operation complete',
      copiedToClipboardSnackbar: 'Copied to clipboard',
      removedItems: 'Sent to trash',
    },
  };

</script>

<style scoped>

</style>
