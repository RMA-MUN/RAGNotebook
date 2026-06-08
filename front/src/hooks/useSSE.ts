import { useRef, useState, useCallback } from 'react'
import type { SSEMessage, KnowledgeSSEMessage } from '../types/api'

type SSECallback = {
  onThinking?: (stage: string, content?: string, details?: Record<string, unknown>) => void
  onResponse?: (content: string, sessionId?: string) => void
  onDone?: (sessionId?: string) => void
  onError?: (error: string) => void
  onKnowledgeProgress?: (data: KnowledgeSSEMessage) => void
}

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = useCallback(
    async (url: string, body: Record<string, unknown> | FormData, callbacks: SSECallback) => {
      setLoading(true)
      setError(null)
      abortRef.current = new AbortController()

      try {
        const token = localStorage.getItem('jwt_token')
        const isFormData = body instanceof FormData

        const response = await fetch(url, {
          method: 'POST',
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
          },
          body: isFormData ? body : JSON.stringify(body),
          signal: abortRef.current.signal,
        })

        if (!response.ok) {
          callbacks.onError?.(`HTTP ${response.status}`)
          setError(`HTTP ${response.status}`)
          setLoading(false)
          return
        }

        const reader = response.body?.getReader()
        if (!reader) {
          callbacks.onError?.('No response body')
          setError('No response body')
          setLoading(false)
          return
        }

        const decoder = new TextDecoder()
        let buffer = ''

        // Response buffer: accumulate chunks and flush in batches
        const responseBuffer: string[] = []
        let lastSessionId: string | undefined
        const RESPONSE_FLUSH_THRESHOLD = 3

        const flushResponse = () => {
          if (responseBuffer.length === 0) return
          const content = responseBuffer.join('')
          responseBuffer.length = 0
          callbacks.onResponse?.(content, lastSessionId)
        }

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))

                // Handle knowledge SSE format
                if (data.event_type) {
                  callbacks.onKnowledgeProgress?.(data as KnowledgeSSEMessage)
                  if (data.event_type === 'finish') {
                    callbacks.onDone?.()
                  }
                  continue
                }

                // Handle chat SSE format
                const msg = data as SSEMessage
                switch (msg.type) {
                  case 'thinking':
                    flushResponse()
                    callbacks.onThinking?.(msg.stage || '', msg.content, msg.details as Record<string, unknown> | undefined)
                    break
                  case 'response':
                    if (msg.session_id) lastSessionId = msg.session_id
                    responseBuffer.push(msg.content || '')
                    if (responseBuffer.length >= RESPONSE_FLUSH_THRESHOLD) {
                      flushResponse()
                    }
                    break
                  case 'done':
                    flushResponse()
                    callbacks.onDone?.(msg.session_id)
                    break
                  case 'error':
                    flushResponse()
                    callbacks.onError?.(msg.content || 'Unknown error')
                    setError(msg.content || 'Unknown error')
                    break
                }
              } catch {
                // skip malformed JSON lines
              }
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'AbortError') {
          callbacks.onError?.(err.message)
          setError(err.message)
        }
      } finally {
        setLoading(false)
      }
    },
    []
  )

  const abort = useCallback(() => {
    abortRef.current?.abort()
    setLoading(false)
  }, [])

  return { start, abort, loading, error }
}
