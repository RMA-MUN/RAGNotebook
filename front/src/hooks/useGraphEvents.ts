import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints } from '../api/endpoints'
import type { GraphSSEEvent } from '../types/graph'

export function useGraphEvents(onEvent?: (ev: GraphSSEEvent) => void) {
  const abortRef = useRef<AbortController | null>(null)
  const [connected, setConnected] = useState(false)

  const subscribe = useCallback(() => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const connect = async () => {
      try {
        const token = localStorage.getItem('jwt_token')
        const res = await fetch(endpoints.graphEvents, {
          method: 'GET',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        })
        if (!res.ok || !res.body) return
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        setConnected(true)
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const ev = JSON.parse(line.slice(6)) as GraphSSEEvent
              if (ev.type === 'ping') continue
              onEvent?.(ev)
            } catch { /* 忽略坏行 */ }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== 'AbortError') {
          // 断线后 3s 重连
          setTimeout(() => { if (!controller.signal.aborted) connect() }, 3000)
        }
      } finally {
        setConnected(false)
      }
    }
    void connect()
  }, [onEvent])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    setConnected(false)
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  return { subscribe, abort, connected }
}