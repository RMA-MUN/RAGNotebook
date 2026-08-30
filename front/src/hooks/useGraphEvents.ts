import { useCallback, useEffect, useRef, useState } from 'react'
import { endpoints } from '../api/endpoints'
import { JWT_KEY } from '../api/client'
import type { GraphSSEEvent } from '../types/graph'

const RECONNECT_BASE_MS = 3000
const RECONNECT_MAX_MS = 30000

export function useGraphEvents(onEvent?: (ev: GraphSSEEvent) => void) {
  const abortRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [connected, setConnected] = useState(false)

  // 回调经 ref 转发，保证 subscribe 身份稳定：上游回调因数据依赖变化时不断开 SSE 连接
  const onEventRef = useRef(onEvent)
  useEffect(() => {
    onEventRef.current = onEvent
  })

  const subscribe = useCallback(() => {
    abortRef.current?.abort()
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    const controller = new AbortController()
    abortRef.current = controller

    let delay = RECONNECT_BASE_MS
    const scheduleReconnect = () => {
      if (controller.signal.aborted || timerRef.current) return
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        if (!controller.signal.aborted) void connect()
      }, delay)
      delay = Math.min(delay * 2, RECONNECT_MAX_MS)
    }

    const connect = async () => {
      try {
        const token = localStorage.getItem(JWT_KEY)
        const res = await fetch(endpoints.graphEvents, {
          method: 'GET',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        })
        if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`)
        setConnected(true)
        delay = RECONNECT_BASE_MS
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
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
              onEventRef.current?.(ev)
            } catch { /* 忽略坏行 */ }
          }
        }
        // 流正常结束（服务端优雅关闭或代理空闲断开）同样视为掉线，交由下方统一重连
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return
      } finally {
        setConnected(false)
      }
      scheduleReconnect()
    }
    void connect()
  }, [])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setConnected(false)
  }, [])

  useEffect(() => () => abort(), [abort])

  return { subscribe, abort, connected }
}
