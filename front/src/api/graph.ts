import client from './client'
import { endpoints } from './endpoints'
import type {
  EntityNoteLink, EntityType, ExtractLog, GraphEntity, GraphSearchResult, GraphView,
} from '../types/graph'
import type { ApiResponse } from '../types/api'

export const graphApi = {
  overview: async (params?: { types?: string; limit?: number }) => {
    const res = await client.get<ApiResponse<GraphView>>(endpoints.graphOverview, { params })
    return res.data.data
  },
  entity: async (id: string) => {
    const res = await client.get<ApiResponse<GraphEntity | null>>(endpoints.graphEntity(id))
    return res.data.data
  },
  neighbors: async (id: string, depth = 1) => {
    const res = await client.get<ApiResponse<GraphView>>(endpoints.graphEntityNeighbors(id), { params: { depth } })
    return res.data.data
  },
  entityNotes: async (id: string) => {
    const res = await client.get<ApiResponse<EntityNoteLink[]>>(endpoints.graphEntityNotes(id))
    return res.data.data
  },
  noteRelated: async (id: string) => {
    const res = await client.get<ApiResponse<GraphView>>(endpoints.graphNoteRelated(id))
    return res.data.data
  },
  docRelated: async (id: string) => {
    const res = await client.get<ApiResponse<GraphView>>(endpoints.graphDocRelated(id))
    return res.data.data
  },
  search: async (q: string) => {
    const res = await client.get<ApiResponse<GraphSearchResult>>(endpoints.graphSearch, { params: { q } })
    return res.data.data
  },
  extractLogs: async (noteId?: string) => {
    const res = await client.get<ApiResponse<ExtractLog[]>>(endpoints.graphExtractLogs, { params: { note_id: noteId } })
    return res.data.data
  },
  createEntity: async (data: Partial<GraphEntity>) => {
    const res = await client.post<ApiResponse<GraphEntity>>(endpoints.graphEntities, data)
    return res.data.data
  },
  updateEntity: async (id: string, data: Partial<GraphEntity>) => {
    const res = await client.put<ApiResponse<GraphEntity>>(endpoints.graphEntityUpdate(id), data)
    return res.data.data
  },
  deleteEntity: async (id: string) => {
    await client.delete(endpoints.graphEntityUpdate(id))
  },
  mergeEntities: async (targetId: string, sourceId: string) => {
    const res = await client.post<ApiResponse<GraphEntity>>(endpoints.graphEntityMerge, { target_id: targetId, source_id: sourceId })
    return res.data.data
  },
  types: async () => {
    const res = await client.get<ApiResponse<EntityType[]>>(endpoints.graphTypes)
    return res.data.data
  },
  reExtract: async (id: string) => {
    const res = await client.post<ApiResponse<{ triggered: boolean }>>(endpoints.graphReExtract(id))
    return res.data.data
  },
}