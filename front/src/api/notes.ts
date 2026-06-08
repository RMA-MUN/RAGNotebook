import client from './client'
import { endpoints } from './endpoints'
import type { ApiResponse, Note, NoteListResponse, RelatedFragment } from '../types/api'

export const notesApi = {
  list: async (params: { page?: number; page_size?: number; category?: string; tag?: string }) => {
    const res = await client.get<ApiResponse<NoteListResponse>>(endpoints.noteList, { params })
    return res.data
  },

  get: async (id: string) => {
    const res = await client.get<ApiResponse<Note>>(endpoints.noteDetail(id))
    return res.data
  },

  create: async (data: { title: string; content: string; category?: string; tags?: string[] }) => {
    const res = await client.post<ApiResponse<Note>>(endpoints.noteCreate, data)
    return res.data
  },

  update: async (id: string, data: Partial<Note>) => {
    const res = await client.put<ApiResponse<Note>>(endpoints.noteUpdate(id), data)
    return res.data
  },

  delete: async (id: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.noteDelete(id))
    return res.data
  },

  search: async (query: string) => {
    const res = await client.get<ApiResponse<NoteListResponse>>(endpoints.noteSearch, { params: { q: query } })
    return res.data
  },

  related: async (id: string) => {
    const res = await client.get<ApiResponse<RelatedFragment[]>>(endpoints.noteRelated(id))
    return res.data
  },

  download: async (id: string) => {
    const res = await client.get<Blob>(endpoints.noteDownload(id), { responseType: 'blob' })
    return res.data
  },

  autocomplete: async (context: string) => {
    const res = await client.post<ApiResponse<{ completion: string }>>(endpoints.noteAutocomplete, { context })
    return res.data
  },
}
