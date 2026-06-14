import client from './client'
import { endpoints } from './endpoints'
import type { ApiResponse, NoteTemplate } from '../types/api'

export const noteTemplatesApi = {
  list: async () => {
    const res = await client.get<ApiResponse<NoteTemplate[]>>(endpoints.noteTemplateList)
    return res.data
  },

  create: async (data: { name: string; icon?: string; category?: string; title?: string; content?: string; tags?: string[] }) => {
    const res = await client.post<ApiResponse<NoteTemplate>>(endpoints.noteTemplateCreate, data)
    return res.data
  },

  update: async (id: string, data: Partial<NoteTemplate>) => {
    const res = await client.put<ApiResponse<NoteTemplate>>(endpoints.noteTemplateUpdate(id), data)
    return res.data
  },

  delete: async (id: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.noteTemplateDelete(id))
    return res.data
  },

  reorder: async (ids: string[]) => {
    const res = await client.put<ApiResponse<null>>(endpoints.noteTemplateReorder, { ids })
    return res.data
  },
}
