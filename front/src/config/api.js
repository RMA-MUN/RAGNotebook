/**
 * API配置文件
 * 包含API基础URL和所有API端点配置
 */

// API基础URL配置
export const apiConfig = {
  // 后端API基础URL（使用相对路径，通过Vite代理访问）
  baseURL: import.meta.env.VITE_BASE_URL || '',
  // 用户服务基础URL（使用相对路径，通过Vite代理访问）
  userBaseURL: import.meta.env.VITE_USER_BASE_URL || '',
  
  // API端点配置
  endpoints: {
    // 认证相关
    login: '/user/login/',
    logout: '/user/logout/',
    register: '/user/register/',
    profile: '/user/detail/',
    
    // 文件上传
    uploadFile: '/file/upload/',
    
    // AI对话相关
    agentQuery: '/chat/agent/query/stream',
    agentQueryStream: '/chat/agent/query/stream',

    // RAG相关
    ragQuery: '/chat/rag/query',

    // 会话管理
    getSession: '/chat/session/',
    deleteSession: '/chat/session/',
    getAllSessions: '/chat/sessions',
    getUserSessions: '/chat/sessions',

    // 向量数据库
    uploadSingleFile: '/knowledge/add/single',
    uploadMultipleFiles: '/knowledge/add/multiple',
    cleanVectors: '/knowledge/clean',

    // 文档重排序
    reorderDocuments: '/chat/reorder',
    
    // 笔记管理
    noteCreate: '/note/create',
    noteUpdate: (noteId) => `/note/${noteId}`,
    noteDelete: (noteId) => `/note/${noteId}`,
    noteDetail: (noteId) => `/note/${noteId}`,
    noteList: '/note/list',
    noteSearch: '/note/search',
    noteAutoTag: (noteId) => `/note/${noteId}/auto-tag`,
    noteRelated: (noteId) => `/note/${noteId}/related`,
    noteDownload: (noteId) => `/note/${noteId}/download`,
    noteAutocomplete: '/note/autocomplete',
    noteAssistStream: '/note/assist/stream',
    
    // 回顾提醒
    reviewToday: '/review/today',
    reviewDone: (noteId) => `/review/done/${noteId}`,
    reviewQuestion: (noteId) => `/review/question/${noteId}`,
  }
}