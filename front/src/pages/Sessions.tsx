import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus, MessageSquare, Trash2 } from 'lucide-react'
import { sessionsApi } from '../api/sessions'
import { useSessionStore } from '../stores/useSessionStore'
import { useUserStore } from '../stores/useUserStore'
import type { ChatSession } from '../types/api'
import EmptyState from '../components/common/EmptyState'
import ConfirmDialog from '../components/common/ConfirmDialog'

export default function Sessions() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const userId = useUserStore((s) => s.userInfo?.uuid || s.userInfo?.user_id || s.userInfo?.id || '')
  const { sessions, setSessions, removeSession, setLoading, loading } = useSessionStore()
  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null)

  const loadSessions = async () => {
    if (!userId) return
    setLoading(true)
    try {
      const res = await sessionsApi.list(userId as string)
      const sessionList = (res.data as { sessions: ChatSession[] } | undefined)?.sessions || []
      setSessions(sessionList as ChatSession[])
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadSessions() }, [userId])

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await sessionsApi.delete(deleteTarget.id)
      removeSession(deleteTarget.id)
    } catch {
      // ignore
    }
    setDeleteTarget(null)
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="max-w-3xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-heading text-xl font-semibold text-[var(--color-text)]">{t('chat.title')}</h1>
        <button
          onClick={() => {
            sessionStorage.removeItem('lastSessionId')
            navigate('/chat')
          }}
          className="flex items-center gap-2 px-4 py-2 rounded-md bg-[var(--color-accent)] text-white text-sm hover:bg-blue-700 transition-colors"
        >
          <Plus size={16} />
          {t('chat.newSession')}
        </button>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 bg-[var(--color-bg-tertiary)] rounded-lg animate-pulse" />
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={<MessageSquare size={48} />}
          message={t('common.noMore')}
          action={
            <button
              onClick={() => {
                sessionStorage.removeItem('lastSessionId')
                navigate('/chat')
              }}
              className="px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white"
            >
              {t('chat.newSession')}
            </button>
          }
        />
      ) : (
        <div className="space-y-2">
          {sessions.map((session) => (
            <div
              key={session.id}
              onClick={() => navigate(`/chat/${session.id}`)}
              className="flex items-center justify-between px-4 py-3 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)] hover:border-[var(--color-accent)] cursor-pointer transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0">
                <MessageSquare size={16} className="text-[var(--color-text-tertiary)] shrink-0" />
                <span className="text-sm text-[var(--color-text)] truncate">{session.title || t('chat.newSession')}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <span className="text-xs text-[var(--color-text-tertiary)]">{formatDate(session.created_at)}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); setDeleteTarget(session) }}
                  className="p-1 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)] transition-colors"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={() => setDeleteTarget(null)}
        title={t('common.confirm')}
        message={t('note.deleteConfirm')}
        variant="danger"
        confirmText={t('note.delete')}
        onConfirm={handleDelete}
      />
    </div>
  )
}
