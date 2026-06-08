import { useEffect, useState } from 'react'
import { Library, FileText, X, ChevronDown, ChevronUp } from 'lucide-react'
import { notesApi } from '../../api/notes'
import type { RelatedFragment } from '../../types/api'

interface Props {
  noteId: string
  open: boolean
  onClose: () => void
}

export default function RelatedFragments({ noteId, open, onClose }: Props) {
  const [fragments, setFragments] = useState<RelatedFragment[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !noteId) return
    setLoading(true)
    notesApi.related(noteId)
      .then((res) => setFragments(res.data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [noteId, open])

  if (!open) return null

  return (
    <div className="w-80 flex flex-col border-l border-[var(--color-border)] bg-[var(--color-card)] shrink-0">
      <div className="flex items-center justify-between px-4 h-12 border-b border-[var(--color-border-light)]">
        <h2 className="text-sm font-medium text-[var(--color-text)]">
          关联片段
          {!loading && <span className="ml-1.5 text-xs text-[var(--color-text-tertiary)]">({fragments.length})</span>}
        </h2>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-[var(--color-border)] border-t-[var(--color-accent)] rounded-full animate-spin" />
          </div>
        ) : fragments.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无关联片段
          </div>
        ) : (
          <div className="p-3 space-y-3">
            {fragments.map((f) => {
              const isExpanded = expandedId === f.id
              return (
                <div
                  key={`${f.source}-${f.id}-${f.content_preview.slice(0, 20)}`}
                  className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-bg)] overflow-hidden"
                >
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : f.id)}
                    className="w-full text-left px-3 pt-3 pb-2 hover:bg-[var(--color-bg-secondary)] transition-colors"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        {f.source === 'knowledge_base' ? (
                          <Library size={13} className="shrink-0 text-blue-500" />
                        ) : (
                          <FileText size={13} className="shrink-0 text-emerald-500" />
                        )}
                        <span className="text-xs font-medium text-[var(--color-text)] truncate">
                          {f.title}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                          f.source === 'knowledge_base'
                            ? 'bg-blue-50 text-blue-600'
                            : 'bg-emerald-50 text-emerald-600'
                        }`}>
                          {f.source === 'knowledge_base' ? '知识库' : '笔记'}
                        </span>
                        {isExpanded ? <ChevronUp size={14} className="text-[var(--color-text-tertiary)]" /> : <ChevronDown size={14} className="text-[var(--color-text-tertiary)]" />}
                      </div>
                    </div>
                    <p className="mt-1.5 text-xs leading-relaxed text-[var(--color-text-secondary)] line-clamp-3">
                      {f.content_preview}
                    </p>
                    <div className="mt-1 text-[10px] text-[var(--color-text-tertiary)]">
                      相似度: {(f.similarity * 100).toFixed(1)}%
                    </div>
                  </button>
                  {isExpanded && (
                    <div className="px-3 pb-3 pt-1 border-t border-[var(--color-border-light)]">
                      <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] whitespace-pre-wrap max-h-60 overflow-y-auto">
                        {f.content}
                      </p>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
