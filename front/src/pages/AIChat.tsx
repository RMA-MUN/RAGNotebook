import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Send, Sparkles, Bot, User, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import { useSSE } from '../hooks/useSSE'
import { sessionsApi } from '../api/sessions'
import { useThemeStore } from '../stores/useThemeStore'

interface Message {
  role: 'user' | 'assistant'
  content: string
  thinking?: string
  steps?: string[]
}

interface ThinkingStep {
  stage: string
  content: string
  details?: Record<string, unknown>
}

const quickQuestions = [
  '帮我解释一下量子计算',
  '写一首关于春天的诗',
  '推荐几本提升思维的书',
]

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
  const [showThinking, setShowThinking] = useState(true)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef('')
  const rafRef = useRef<number | null>(null)

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

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [])

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
          setThinkingSteps((prev) => [...prev, { stage, content: content || '', details }])
          setCurrentThinking(prev => prev ? `${prev}\n${content}` : (content || ''))
        },
        onResponse: (content, sessionId) => {
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
              flushContent()
            })
          }
        },
        onDone: (newSessionId) => {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current)
            rafRef.current = null
          }
          flushContent()
          if (newSessionId) {
            sessionStorage.setItem('lastSessionId', newSessionId)
          }
          if (newSessionId && newSessionId !== sessionId) {
            navigate(`/chat/${newSessionId}`, { replace: true })
          }
        },
        onError: (error) => {
          setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${error}` }])
        },
      }
    )
  }, [loading, sessionId, start, navigate, flushContent])

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
        onClick={() => setShowThinking(!showThinking)}
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
                <span className="text-[var(--color-text-tertiary)]">{step.details?.source ? String(step.details.source) : ''}</span>
              </summary>
              <div className="px-3 pb-3 space-y-2 text-xs text-[var(--color-text-secondary)]">
                <p className="leading-relaxed whitespace-pre-wrap">{step.content}</p>
                {step.details && Object.keys(step.details).length > 0 && (
                  <pre className="overflow-x-auto rounded bg-[var(--color-bg)] p-2 leading-relaxed whitespace-pre-wrap break-words">
                    {JSON.stringify(step.details, null, 2)}
                  </pre>
                )}
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
                      <ReactMarkdown rehypePlugins={[rehypeHighlight, rehypeRaw]}>
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
