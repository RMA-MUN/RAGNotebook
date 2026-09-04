import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Send, Sparkles, Bot, User, ChevronDown, ChevronRight, Loader2, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import remarkGfm from 'remark-gfm'
import { useSSE } from '../hooks/useSSE'
import { sessionsApi } from '../api/sessions'
import { useThemeStore } from '../stores/useThemeStore'
import { isEvidenceEvent, isReadableRetrievalStatus, isRetrievalStage, isVisibleThinkingStage, mergeThinkingStep, previewEvidence, retrievalStatusLabel, toEvidence } from '../utils/thinkingTrace'
import type { ThinkingStep } from '../utils/thinkingTrace'

interface Message {
  role: 'user' | 'assistant'
  content: string
  thinking?: string
  steps?: string[]
}

const quickQuestions = [
  '帮我解释一下量子计算',
  '写一首关于春天的诗',
  '推荐几本提升思维的书',
]

const evidenceSourceLabels: Record<string, string> = {
  knowledge_base: 'Knowledge base',
  web_search: 'Web search',
}

const retrievalToolLabels: Record<string, string> = {
  hybrid_search: '混合检索',
  search_graph: '知识图谱检索',
  search_notes: '笔记检索',
  search_knowledge_base: '知识库检索',
  web_search: 'Web 检索',
}

export default function AIChat() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const theme = useThemeStore((s) => s.theme)
  const { start, loading } = useSSE()
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [currentThinking, setCurrentThinking] = useState('')
  const [thinkingSteps, setThinkingSteps] = useState<ThinkingStep[]>([])
  const [expandedEvidence, setExpandedEvidence] = useState<Record<string, boolean>>({})
  const [showThinking, setShowThinking] = useState(true)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef('')
  const rafRef = useRef<number | null>(null)
  const pendingThinkingRef = useRef<ThinkingStep[]>([])
  const thinkingTimerRef = useRef<number | null>(null)
  const thinkingGenerationRef = useRef(0)
  const thinkingManuallyCollapsedRef = useRef(false)
  const thinkingTerminalGenerationRef = useRef<number | null>(null)

  const logicalThinkingKey = (step: ThinkingStep) => {
    if (step.stage !== 'supplemental_retrieval') return step.stage
    const query = step.details?.query
    return `${step.stage}:${typeof query === 'string' ? query : ''}`
  }

  const mergeIncomingThinkingStep = (steps: ThinkingStep[], next: ThinkingStep) => {
    const nextKey = logicalThinkingKey(next)
    const placeholderIndex = steps.findIndex((step) => (
      logicalThinkingKey(step) === nextKey && step.details?.placeholder
    ))
    if (placeholderIndex !== -1) {
      const replaced = [...steps]
      replaced[placeholderIndex] = next
      return replaced
    }

    if (isRetrievalStage(next.stage)) {
      const existingIdx = steps.findIndex((step) => logicalThinkingKey(step) === nextKey)
      if (existingIdx !== -1) {
        const merged = [...steps]
        merged[existingIdx] = mergeThinkingStep(merged[existingIdx], next)
        return merged
      }
    }

    return [...steps, next]
  }

  const flushContent = useCallback(() => {
    setMessages((prev) => {
      const newMsgs = [...prev]
      const last = newMsgs[newMsgs.length - 1]
      if (last?.role === 'assistant') {
        newMsgs[newMsgs.length - 1] = { ...last, content: contentRef.current }
      } else {
        newMsgs.push({ role: 'assistant', content: contentRef.current })
      }
      return newMsgs
    })
  }, [])

  const cancelPendingThinking = useCallback(() => {
    thinkingGenerationRef.current += 1
    pendingThinkingRef.current = []
    if (thinkingTimerRef.current !== null) {
      clearTimeout(thinkingTimerRef.current)
      thinkingTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      cancelPendingThinking()
    }
  }, [cancelPendingThinking])

  useEffect(() => {
    if (sessionId) {
      setLoadingHistory(true)
      sessionsApi.get(sessionId).then((res) => {
        const data = res.data as { history?: [string, string][] } | undefined
        if (data?.history) {
          setMessages(data.history.flatMap(([query, response]) => [
            { role: 'user', content: query },
            { role: 'assistant', content: response },
          ]))
        }
      }).catch(() => {}).finally(() => setLoadingHistory(false))
    }
  }, [sessionId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentThinking])

  useEffect(() => {
    if (!sessionId) {
      const lastId = sessionStorage.getItem('lastSessionId')
      if (lastId) {
        navigate(`/chat/${lastId}`, { replace: true })
      }
    }
  }, [sessionId, navigate])

  const handleSend = useCallback(async (query: string) => {
    if (!query.trim() || loading) return

    const userMsg: Message = { role: 'user', content: query }
    cancelPendingThinking()
    const requestGeneration = thinkingGenerationRef.current
    thinkingManuallyCollapsedRef.current = false
    thinkingTerminalGenerationRef.current = null
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setCurrentThinking('')
    setThinkingSteps([])
    setShowThinking(true)

    contentRef.current = ''
    let hasResponseStarted = false

    await start(
      '/chat/agent/query/stream',
      { query, session_id: sessionId },
      {
        onThinking: (stage, content, details) => {
          if (thinkingGenerationRef.current !== requestGeneration || thinkingTerminalGenerationRef.current === requestGeneration) return
          if (!isVisibleThinkingStage(stage)) return
          if (!thinkingManuallyCollapsedRef.current) setShowThinking(true)
          const results = isEvidenceEvent(stage, details) && Array.isArray(details?.results)
            ? details.results.map(toEvidence).filter((item) => item !== null)
            : []
          if (isEvidenceEvent(stage, details) && results.length === 0) return
          const step: ThinkingStep = {
            stage,
            content: content || '',
            details,
            ...(results.length > 0 ? { evidence: results } : {}),
          }
          if (details?.status === 'searching') {
            setThinkingSteps((prev) => {
              const nextKey = logicalThinkingKey(step)
              const existingIdx = prev.findIndex((item) => logicalThinkingKey(item) === nextKey)
              if (existingIdx === -1) return [...prev, step]
              const merged = [...prev]
              merged[existingIdx] = mergeThinkingStep(merged[existingIdx], step)
              return merged
            })
            return
          }
          const isPlaceholder = Boolean(details?.placeholder)
          if (isPlaceholder) {
            // 占位事件立即落地，让「正在规划」折叠框第一时间出现
            setThinkingSteps((prev) => [...prev, step])
            return
          }
          // 真实步骤加入待渲染队列，按 150ms 间隔逐个 flush，形成依次推进的节奏；
          // 若上一步是占位（同 stage），则替换而非新增，避免叠两条。
          pendingThinkingRef.current.push(step)
          setCurrentThinking((prev) => prev ? `${prev}\n${content}` : (content || ''))
          if (thinkingTimerRef.current !== null) return
          const flushOne = () => {
            if (thinkingGenerationRef.current !== requestGeneration) {
              thinkingTimerRef.current = null
              return
            }
            const next = pendingThinkingRef.current.shift()
            if (!next) {
              thinkingTimerRef.current = null
              return
            }
            setThinkingSteps((prev) => {
              return mergeIncomingThinkingStep(prev, next)
            })
            thinkingTimerRef.current = window.setTimeout(flushOne, 150)
          }
          thinkingTimerRef.current = window.setTimeout(flushOne, 150)
        },
        onResponse: (content, sessionId) => {
          if (thinkingGenerationRef.current !== requestGeneration || thinkingTerminalGenerationRef.current === requestGeneration) return
          if (!hasResponseStarted) {
            hasResponseStarted = true
          }
          if (sessionId) {
            sessionStorage.setItem('lastSessionId', sessionId)
          }
          contentRef.current += content
          if (rafRef.current === null) {
            rafRef.current = requestAnimationFrame(() => {
              rafRef.current = null
              if (thinkingGenerationRef.current !== requestGeneration || thinkingTerminalGenerationRef.current === requestGeneration) return
              flushContent()
            })
          }
        },
        onDone: (newSessionId) => {
          if (thinkingGenerationRef.current !== requestGeneration || thinkingTerminalGenerationRef.current === requestGeneration) return
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current)
            rafRef.current = null
          }
          flushContent()
          const pending = pendingThinkingRef.current.splice(0)
          if (pending.length > 0) {
            setThinkingSteps((prev) => pending.reduce(mergeIncomingThinkingStep, prev))
          }
          cancelPendingThinking()
          setShowThinking(false)
          if (newSessionId) {
            sessionStorage.setItem('lastSessionId', newSessionId)
          }
          if (newSessionId && newSessionId !== sessionId) {
            navigate(`/chat/${newSessionId}`, { replace: true })
          }
        },
        onError: (error) => {
          if (thinkingGenerationRef.current !== requestGeneration || thinkingTerminalGenerationRef.current === requestGeneration) return
          const pending = pendingThinkingRef.current.splice(0)
          if (pending.length > 0) {
            setThinkingSteps((prev) => pending.reduce(mergeIncomingThinkingStep, prev))
          }
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current)
            rafRef.current = null
          }
          thinkingTerminalGenerationRef.current = requestGeneration
          cancelPendingThinking()
          setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${error}` }])
        },
      }
    )
  }, [loading, sessionId, start, navigate, flushContent, cancelPendingThinking])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(input)
    }
  }

  const isLoading = loadingHistory || loading
  const hasStreamingAssistant = loading && messages.length > 0 && messages[messages.length - 1].role === 'assistant'

  const thinkingPanel = thinkingSteps.length > 0 ? (
    <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] overflow-hidden">
      <button
        onClick={() => setShowThinking((previous) => {
          const next = !previous
          thinkingManuallyCollapsedRef.current = !next
          return next
        })}
        className="flex items-center justify-between gap-2 px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] w-full text-left"
      >
        <span className="flex items-center gap-2">
          {showThinking ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {t('chat.thinkingSteps')} ({thinkingSteps.length})
        </span>
        {loading && <Loader2 size={13} className="animate-spin" />}
      </button>
      {showThinking && (
        <div className="px-4 pb-3 space-y-2">
          {thinkingSteps.map((step, index) => (
            <details key={`${step.stage}-${index}`} open={index === thinkingSteps.length - 1} className="rounded-md bg-[var(--color-bg-secondary)] border border-[var(--color-border)]">
              <summary className="cursor-pointer list-none px-3 py-2 text-xs text-[var(--color-text)] flex items-center justify-between gap-3">
                <span className="font-medium">{index + 1}. {step.stage}</span>
                <span className="text-[var(--color-text-tertiary)]">
                  {isReadableRetrievalStatus(step.stage, step.details) ? retrievalStatusLabel(step.details?.status, step.stage) : ''}
                </span>
              </summary>
              <div className="px-3 pb-3 space-y-2 text-xs text-[var(--color-text-secondary)]">
                {isReadableRetrievalStatus(step.stage, step.details) && (
                  <p className="leading-relaxed whitespace-pre-wrap">{retrievalStatusLabel(step.details?.status, step.stage)}</p>
                )}
                {step.stage === 'agentic_plan' && (
                  <p className="leading-relaxed whitespace-pre-wrap">{step.content}</p>
                )}
                {step.stage === 'agentic_plan' && step.details && (
                  <div className="space-y-1 text-[var(--color-text-tertiary)]">
                    {typeof step.details.reason === 'string' && <p>Reason: {step.details.reason}</p>}
                    {typeof step.details.source === 'string' && <p>Source: {step.details.source}</p>}
                    {typeof step.details.step_count === 'number' && <p>Retrieval steps: {step.details.step_count}</p>}
                    {Array.isArray(step.details.steps) && step.details.steps.length > 0 && (
                      <div className="pt-2 space-y-2">
                        <p className="font-medium text-[var(--color-text-secondary)]">具体调用</p>
                        {step.details.steps.map((rawStep, planIndex) => {
                          if (typeof rawStep !== 'object' || rawStep === null || Array.isArray(rawStep)) return null
                          const planStep = rawStep as Record<string, unknown>
                          const tool = typeof planStep.tool === 'string' ? planStep.tool : 'unknown'
                          const query = typeof planStep.query === 'string' ? planStep.query : ''
                          const topK = typeof planStep.top_k === 'number' ? planStep.top_k : null
                          return (
                            <div key={`${tool}-${planIndex}`} className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-2 space-y-1">
                              <p className="text-[var(--color-text)]">{retrievalToolLabels[tool] || tool}</p>
                              {query && <p>查询：{query}</p>}
                              {topK !== null && <p>数量：{topK}</p>}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
                {isEvidenceEvent(step.stage, step.details) && step.evidence && step.evidence.length > 0 ? (
                  <div className="space-y-2">
                    {step.evidence.map((evidence, evidenceIndex) => (
                      (() => {
                        const evidenceKey = `${step.stage}:${evidence.source}:${evidence.id}:${evidenceIndex}`
                        const isExpanded = expandedEvidence[evidenceKey] === true
                        const hasMore = evidence.preview.length > 200
                        return <div key={evidenceKey} className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] p-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-medium text-[var(--color-text)]">{evidence.title}</span>
                          {evidence.score !== undefined && evidence.score !== null && <span>{evidence.score}</span>}
                        </div>
                        <div className="text-[var(--color-text-tertiary)]">{evidenceSourceLabels[evidence.source] || evidence.source || 'Unknown source'}</div>
                        <p className="mt-1 whitespace-pre-wrap">{isExpanded ? evidence.preview : previewEvidence(evidence.preview)}</p>
                        {hasMore && (
                          <button
                            type="button"
                            aria-expanded={isExpanded}
                            aria-label={isExpanded ? 'Collapse evidence' : 'Expand evidence'}
                            onClick={() => setExpandedEvidence((previous) => ({ ...previous, [evidenceKey]: !isExpanded }))}
                            className="mt-1 inline-flex items-center gap-1 text-[var(--color-accent)] hover:underline"
                          >
                            {isExpanded ? <ChevronsUpDown size={13} /> : <ChevronsDownUp size={13} />}
                            {isExpanded ? '收起' : '展开全文'}
                          </button>
                        )}
                      </div>
                      })()
                    ))}
                  </div>
                ) : null}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  ) : null

  return (
    <div className="h-full flex flex-col">
      {messages.length > 0 && (
        <div className="shrink-0 px-6 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <div className="max-w-3xl mx-auto flex justify-end">
            <button
              onClick={() => {
                sessionStorage.removeItem('lastSessionId')
                setMessages([])
                navigate('/chat')
              }}
              className="px-3 py-1.5 text-xs rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
            >
              {t('chat.newSession')}
            </button>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && !isLoading && (
            <div className="py-16 text-center space-y-6">
              <div className="flex justify-center">
                <div className="w-16 h-16 rounded-2xl bg-[var(--color-accent-bg)] flex items-center justify-center">
                  <Sparkles size={28} className="text-[var(--color-accent)]" />
                </div>
              </div>
              <h2 className="font-heading text-xl text-[var(--color-text)]">{t('chat.welcome')}</h2>
              <div className="flex flex-wrap justify-center gap-2 max-w-md mx-auto">
                {quickQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="px-4 py-2 text-xs rounded-full border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {loadingHistory && (
            <div className="flex justify-center py-4">
              <Loader2 size={20} className="animate-spin text-[var(--color-text-tertiary)]" />
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
              {msg.role === 'assistant' && (
                <div className="w-8 h-8 rounded-lg bg-[var(--color-accent-bg)] flex items-center justify-center shrink-0">
                  <Bot size={16} className="text-[var(--color-accent)]" />
                </div>
              )}
              <div className={`max-w-[75%] ${msg.role === 'user' ? 'order-first' : ''}`}>
                {msg.role === 'user' ? (
                  <div className="px-4 py-2.5 rounded-2xl bg-[var(--color-accent)] text-white text-sm">
                    {msg.content}
                  </div>
                ) : (
                  <>
                    {i === messages.length - 1 && thinkingPanel && (
                      <div className="mb-3">
                        {thinkingPanel}
                      </div>
                    )}
                    <div className={`prose prose-sm max-w-none markdown-body${theme === 'dark' ? ' prose-invert' : ''}`}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                    {hasStreamingAssistant && i === messages.length - 1 && (
                      <div className="flex gap-1 mt-3">
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '300ms' }} />
                      </div>
                    )}
                  </>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-8 h-8 rounded-lg bg-[var(--color-bg-tertiary)] flex items-center justify-center shrink-0">
                  <User size={16} className="text-[var(--color-text-secondary)]" />
                </div>
              )}
            </div>
          ))}

          {loading && !hasStreamingAssistant && (
            <div className="flex gap-3">
              <div className="w-8 h-8 rounded-lg bg-[var(--color-accent-bg)] flex items-center justify-center shrink-0">
                <Bot size={16} className="text-[var(--color-accent)]" />
              </div>
              <div className="space-y-2 flex-1">
                {thinkingPanel}
                <div className="flex gap-1">
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="border-t border-[var(--color-border)] bg-[var(--color-card)] px-6 py-4">
        <div className="max-w-3xl mx-auto flex gap-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('chat.input')}
            rows={1}
            className="flex-1 px-4 py-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] resize-none focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
          />
          <button
            onClick={() => handleSend(input)}
            disabled={!input.trim() || loading}
            className="flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--color-accent)] text-white hover:bg-blue-700 disabled:opacity-40 transition-colors shrink-0"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
