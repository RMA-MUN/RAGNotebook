import { endpoints } from './endpoints'

export const chatApi = {
  queryStream: (body: { query: string; session_id?: string }) => {
    const token = localStorage.getItem('jwt_token')
    return fetch(endpoints.agentQueryStream, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })
  },
}
