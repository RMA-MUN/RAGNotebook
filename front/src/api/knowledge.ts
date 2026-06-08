import client from './client'
import { endpoints } from './endpoints'
import type { ApiResponse, KnowledgeDocument, KnowledgeDocumentDetail } from '../types/api'

interface KnowledgeListData {
  documents: KnowledgeDocument[]
  total_count: number
}

export const knowledgeApi = {
  list: async () => {
    const res = await client.get<ApiResponse<KnowledgeListData>>(endpoints.knowledgeList)
    return res.data
  },

  detail: async (filename: string) => {
    const res = await client.get<ApiResponse<KnowledgeDocumentDetail>>(endpoints.knowledgeDetail, { params: { filename } })
    return res.data
  },

  chunks: async (filename: string) => {
    const res = await client.get<ApiResponse<unknown[]>>(endpoints.knowledgeChunks, { params: { filename } })
    return res.data
  },

  deleteByFilename: async (filename: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.knowledgeDeleteFilename, { params: { filename } })
    return res.data
  },

  deleteByMd5: async (md5: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.knowledgeMd5Delete(md5))
    return res.data
  },

  cleanAll: async () => {
    const res = await client.delete<ApiResponse<null>>(endpoints.cleanVectors)
    return res.data
  },
}
