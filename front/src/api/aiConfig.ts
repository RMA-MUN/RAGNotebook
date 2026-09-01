import client from './client'
import { endpoints } from './endpoints'
import type { AIConfig, AIConfigPayload } from '../types/api'

export const aiConfigApi = {
  get: async (): Promise<AIConfig> => {
    const res = await client.get<{ data: AIConfig }>(endpoints.aiConfig)
    return res.data.data
  },
  save: async (payload: AIConfigPayload): Promise<void> => {
    await client.put<{ data: null }>(endpoints.aiConfig, payload)
  },
}
