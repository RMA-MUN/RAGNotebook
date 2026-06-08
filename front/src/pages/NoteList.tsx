import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus, Search, FileText, Tag } from 'lucide-react'
import { notesApi } from '../api/notes'
import type { Note, NoteListResponse } from '../types/api'
import EmptyState from '../components/common/EmptyState'
import TagBadge from '../components/common/TagBadge'

const categories = [
  { label: '全部', value: '' },
  { label: '工作', value: 'work' },
  { label: '学习', value: 'study' },
  { label: '生活', value: 'life' },
  { label: '技术', value: 'project' },
  { label: '其他', value: 'other' },
]

const CATEGORY_LABEL_MAP: Record<string, string> = {
  work: '工作',
  study: '学习',
  life: '生活',
  project: '技术',
  other: '其他',
}

export default function NoteList() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [notes, setNotes] = useState<Note[]>([])
  const [page, setPage] = useState(1)
  const [category, setCategory] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const loadNotes = useCallback(async (pageNum: number, reset = false) => {
    setLoading(true)
    try {
      let result: { data?: NoteListResponse; message?: string }
      if (searchQuery) {
        result = await notesApi.search(searchQuery)
      } else {
        result = await notesApi.list({
          page: pageNum,
          page_size: 20,
          category: category || undefined,
        })
      }
      const items = (result.data?.notes || []) as Note[]
      const totalCount = result.data?.total_count || 0
      if (reset) {
        setNotes(items)
      } else {
        setNotes((prev) => [...prev, ...items])
      }
      setHasMore(pageNum * 20 < totalCount)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [category, searchQuery])

  useEffect(() => {
    setPage(1)
    loadNotes(1, true)
  }, [category, searchQuery])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasMore && !loading && page > 1) {
          loadNotes(page)
        }
      },
      { threshold: 0.1 }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMore, loading, page])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    setPage(1)
    loadNotes(1, true)
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  }

  return (
    <div className="max-w-4xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-heading text-xl font-semibold text-[var(--color-text)]">{t('note.title')}</h1>
        <button
          onClick={() => navigate('/notes/new')}
          className="flex items-center gap-2 px-4 py-2 rounded-md bg-[var(--color-accent)] text-white text-sm hover:bg-blue-700 transition-colors"
        >
          <Plus size={16} />
          {t('note.newNote')}
        </button>
      </div>

      <div className="flex gap-4 mb-6">
        <form onSubmit={handleSearch} className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-placeholder)]" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('note.search')}
            className="w-full pl-9 pr-4 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
          />
        </form>
      </div>

      <div className="flex gap-2 mb-6 flex-wrap">
        {categories.map((cat) => (
          <button
            key={cat.value}
            onClick={() => setCategory(cat.value)}
            className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
              category === cat.value
                ? 'bg-[var(--color-accent-bg)] text-[var(--color-accent)]'
                : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]'
            }`}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {notes.length === 0 && !loading ? (
        <EmptyState
          icon={<FileText size={48} />}
          message={t('note.empty')}
          action={
            <button onClick={() => navigate('/notes/new')} className="px-4 py-2 text-sm rounded-md bg-[var(--color-accent)] text-white">
              {t('note.newNote')}
            </button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {notes.map((note) => (
            <div
              key={note.id}
              onClick={() => navigate(`/notes/${note.id}`)}
              className="px-5 py-4 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)] hover:border-[var(--color-accent)] cursor-pointer transition-colors"
            >
              <div className="flex items-start justify-between mb-1">
                <h3 className="text-sm font-medium text-[var(--color-text)]">{note.title || '无标题'}</h3>
                <span className="text-xs text-[var(--color-text-tertiary)] shrink-0 ml-3">{formatDate(note.created_at)}</span>
              </div>
              <p className="text-xs text-[var(--color-text-secondary)] line-clamp-2 mb-2">{note.content?.slice(0, 200)}</p>
              <div className="flex items-center gap-2 flex-wrap">
                {note.tags?.map((tag: string) => (
                  <TagBadge key={tag} tag={tag} />
                ))}
                {note.category && (
                  <span className="flex items-center gap-1 text-xs text-[var(--color-text-tertiary)]">
                    <Tag size={10} />
                    {CATEGORY_LABEL_MAP[note.category] || note.category}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <div ref={sentinelRef} className="h-4" />
      {loading && (
        <div className="flex justify-center py-4">
          <div className="w-5 h-5 border-2 border-[var(--color-border)] border-t-[var(--color-accent)] rounded-full animate-spin" />
        </div>
      )}
    </div>
  )
}
