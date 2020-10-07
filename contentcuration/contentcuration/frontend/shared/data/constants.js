export const CHANGE_TYPES = {
  CREATED: 1,
  UPDATED: 2,
  DELETED: 3,
  MOVED: 4,
  COPIED: 5,
};

// Tables
export const CHANGES_TABLE = '__changesForSyncing';

export const CHANGE_LOCKS_TABLE = '__changeLocks';

export const TABLE_NAMES = {
  CHANNEL: 'channel',
  INVITATION: 'invitation',
  CONTENTNODE: 'contentnode',
  CONTENTNODE_PREREQUISITE: 'contentnode_prerequisite',
  CHANNELSET: 'channelset',
  TREE: 'tree',
  ASSESSMENTITEM: 'assessmentitem',
  FILE: 'file',
  USER: 'user',
  CHANNELUSER: 'channeluser',
  EDITOR_M2M: 'editor_m2m',
  VIEWER_M2M: 'viewer_m2m',
  SAVEDSEARCH: 'savedsearch',
  CLIPBOARD: 'clipboard',
  CHANGES_TABLE,
  CHANGE_LOCKS_TABLE,
};

export const MESSAGES = {
  FETCH_COLLECTION: 'FETCH_COLLECTION',
  FETCH_MODEL: 'FETCH_MODEL',
  REQUEST_RESPONSE: 'REQUEST_RESPONSE',
};

export const STATUS = {
  SUCCESS: 'SUCCESS',
  FAILURE: 'FAILURE',
};

export const APP_ID = 'KolibriStudio';

// Transaction sources
/**
 * This transaction source will be ignored when tracking the
 * client's changes
 *
 * @type {string}
 */
export const IGNORED_SOURCE = 'IGNORED_SOURCE';

export const REVERT_SOURCE = 'REVERT/' + IGNORED_SOURCE;

export const RELATIVE_TREE_POSITIONS = {
  FIRST_CHILD: 'first-child',
  LAST_CHILD: 'last-child',
  LEFT: 'left',
  RIGHT: 'right',
};
