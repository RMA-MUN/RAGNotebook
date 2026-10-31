import client from './client'
import { endpoints } from './endpoints'
import type { ApiResponse, ApprovalPayload, ChatSession } from '../types/api'

interface SessionsData {
  sessions: ChatSession[]
}

interface SessionDetailData {
  session_id: string
  history: [string, string][]
  pending_run_id?: string | null
}

interface PendingData {
  run_id: string
  payload: ApprovalPayload
  query?: string
}

export const sessionsApi = {
  list: async (userId: string) => {
    const res = await client.get<ApiResponse<SessionsData>>(endpoints.getUserSessions(userId))
    return res.data
  },

  get: async (id: string) => {
    const res = await client.get<ApiResponse<SessionDetailData>>(endpoints.getSession(id))
    return res.data
  },

  getPending: async (id: string) => {
    const res = await client.get<ApiResponse<PendingData | null>>(endpoints.getSessionPending(id))
    return res.data
  },

  delete: async (id: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.deleteSession(id))
    return res.data
  },
}
