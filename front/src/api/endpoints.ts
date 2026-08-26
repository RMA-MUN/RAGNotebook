export const endpoints = {
  // Auth
  login: '/user/login/',
  logout: '/user/logout/',
  register: '/user/register/',
  profile: '/user/detail/',
  userUpdate: '/user/update/',
  changePassword: '/user/reset-password/',

  // File upload
  uploadFile: '/file/upload/',

  // AI Chat
  agentQueryStream: '/chat/agent/query/stream',
  ragQuery: '/chat/rag/query',

  // Sessions
  getSession: (id: string) => `/chat/session/${id}`,
  deleteSession: (id: string) => `/chat/session/${id}`,
  getAllSessions: '/chat/sessions',
  getUserSessions: (userId: string) => `/chat/sessions/${userId}`,

  // Knowledge Base
  uploadSingleFile: '/knowledge/add/single',
  uploadMultipleFiles: '/knowledge/add/multiple',
  uploadMultipleStream: '/knowledge/add/multiple/stream',
  cleanVectors: '/knowledge/clean',
  knowledgeList: '/knowledge/list',
  knowledgeDetail: '/knowledge/detail',
  knowledgeChunks: '/knowledge/chunks',
  knowledgeImage: (md5: string, filename: string) => `/knowledge/image/${md5}/${filename}`,
  knowledgeMd5List: '/knowledge/md5/list',
  knowledgeMd5Delete: (md5: string) => `/knowledge/md5/delete/${md5}`,
  knowledgeDeleteFilename: '/knowledge/delete/filename',

  // Documents reorder
  reorderDocuments: '/chat/reorder',

  // Notes
  noteCreate: '/note/create',
  noteUpdate: (id: string) => `/note/${id}`,
  noteDelete: (id: string) => `/note/${id}`,
  noteDetail: (id: string) => `/note/${id}`,
  noteList: '/note/list',
  noteSearch: '/note/search',
  noteAutoTag: (id: string) => `/note/${id}/auto-tag`,
  noteRelated: (id: string) => `/note/${id}/related`,
  noteDownload: (id: string) => `/note/${id}/download`,
  notePin: (id: string) => `/note/${id}/pin`,
  noteAutocomplete: '/note/autocomplete',
  noteStats: '/note/stats',
  noteAssistStream: '/note/assist/stream',

  // Batch operations
  noteBatchDelete: '/note/batch/delete',
  noteBatchDownload: '/note/batch/download',
  noteBatchCategory: '/note/batch/category',
  noteBatchPin: '/note/batch/pin',
  noteCategoryDelete: (category: string) => `/note/category/${encodeURIComponent(category)}`,

  // Review
  reviewToday: '/review/today',
  reviewDone: (id: string) => `/review/done/${id}`,
  reviewQuestion: (id: string) => `/review/question/${id}`,

  // Note Templates
  noteTemplateList: '/note-template/list',
  noteTemplateCreate: '/note-template/create',
  noteTemplateUpdate: (id: string) => `/note-template/${id}`,
  noteTemplateDelete: (id: string) => `/note-template/${id}`,
  noteTemplateReorder: '/note-template/reorder',

  // Knowledge Graph
  graphOverview: '/api/graph/overview',
  graphEvents: '/api/graph/events',
  graphEntity: (id: string) => `/api/graph/entity/${id}`,
  graphEntityNeighbors: (id: string) => `/api/graph/entity/${id}/neighbors`,
  graphEntityNotes: (id: string) => `/api/graph/entity/${id}/notes`,
  graphNoteRelated: (id: string) => `/api/graph/notes/${id}/related`,
  graphSearch: '/api/graph/search',
  graphExtractLogs: '/api/graph/extract-logs',
  graphEntities: '/api/graph/entities',
  graphEntityMerge: '/api/graph/entities/merge',
  graphTypes: '/api/graph/types',
  graphRelations: '/api/graph/relations',
  graphReExtract: (id: string) => `/api/graph/notes/${id}/re-extract`,
} as const
