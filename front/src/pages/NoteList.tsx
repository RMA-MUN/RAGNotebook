import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Plus, Search, FileText, Tag, CheckSquare, Square, Settings2, Pin } from 'lucide-react'
import { notesApi } from '../api/notes'
import type { Note, NoteListResponse } from '../types/api'
import EmptyState from '../components/common/EmptyState'
import TagBadge from '../components/common/TagBadge'
import ConfirmDialog from '../components/common/ConfirmDialog'
import BatchActionBar from '../components/note/BatchActionBar'
import CategoryManageDialog from '../components/note/CategoryManageDialog'

const PREDEFINED_CATEGORIES = [
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

const PREDEFINED_VALUES = new Set(['work', 'study', 'life', 'project', 'other'])

const CATEGORY_ORDER_KEY = 'note_category_order'

function getSavedOrder(): string[] {
  try {
    const raw = localStorage.getItem(CATEGORY_ORDER_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function buildCategoryList(customCategories: string[]) {
  const list = PREDEFINED_CATEGORIES.slice()
  for (const cat of customCategories) {
    if (!PREDEFINED_VALUES.has(cat)) {
      list.push({ label: cat, value: cat })
    }
  }

  const order = getSavedOrder()
  if (order.length === 0) return list

  const orderIndex = new Map(order.map((v, i) => [v, i]))

  return list.sort((a, b) => {
    if (a.value === '') return -1
    if (b.value === '') return 1
    const ai = orderIndex.get(a.value)
    const bi = orderIndex.get(b.value)
    if (ai !== undefined && bi !== undefined) return ai - bi
    if (ai !== undefined) return -1
    if (bi !== undefined) return 1
    return 0
  })
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

  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const pressStartPos = useRef<{ x: number; y: number }>({ x: 0, y: 0 })
  const enteredViaLongPress = useRef(false)
  const pointerMoved = useRef(false)

  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [categoryModalOpen, setCategoryModalOpen] = useState(false)
  const [customCategory, setCustomCategory] = useState('')
  const [allCategories, setAllCategories] = useState(PREDEFINED_CATEGORIES)
  const [manageOpen, setManageOpen] = useState(false)
  const [extraCategories, setExtraCategories] = useState<string[]>([])
  const [categoryCounts, setCategoryCounts] = useState<Record<string, number>>({})

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

  const refreshCategories = useCallback(async (extra: string[] = []) => {
    try {
      const res = await notesApi.stats()
      const statsCats = res.data?.categories ?? []
      const counts: Record<string, number> = {}
      for (const c of statsCats) {
        counts[c.category] = c.count
      }
      setCategoryCounts(counts)
      const all = [...new Set([...statsCats.map((c) => c.category), ...extra])]
      setAllCategories(buildCategoryList(all))
    } catch {
      setAllCategories(buildCategoryList(extra))
    }
  }, [])

  useEffect(() => {
    setPage(1)
    loadNotes(1, true)
  }, [category, searchQuery])

  useEffect(() => {
    refreshCategories(extraCategories)
  }, [extraCategories])

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

  const handlePin = async (e: React.MouseEvent, noteId: string) => {
    e.stopPropagation()
    try {
      await notesApi.pin(noteId)
      setNotes((prev) => {
        const updated = prev.map((n) =>
          n.id === noteId ? { ...n, is_pinned: !n.is_pinned } : n
        )
        updated.sort((a, b) => {
          if (a.is_pinned && !b.is_pinned) return -1
          if (!a.is_pinned && b.is_pinned) return 1
          return 0
        })
        return updated
      })
    } catch {
      // ignore
    }
  }

  const clearLongPress = () => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current)
      longPressTimer.current = undefined
    }
  }

  const handlePointerDown = (noteId: string, e: React.PointerEvent) => {
    pressStartPos.current = { x: e.clientX, y: e.clientY }
    enteredViaLongPress.current = false
    pointerMoved.current = false
    clearLongPress()
    longPressTimer.current = setTimeout(() => {
      longPressTimer.current = undefined
      if (!selectMode) {
        enteredViaLongPress.current = true
        setSelectMode(true)
        setSelectedIds(new Set([noteId]))
      }
    }, 500)
  }

  const handlePointerUp = (noteId: string) => {
    const timerWasSet = longPressTimer.current !== undefined
    const wasLongPress = enteredViaLongPress.current
    enteredViaLongPress.current = false
    clearLongPress()

    if (wasLongPress || pointerMoved.current) {
      return
    }
    if (selectMode) {
      toggleNoteSelection(noteId)
    } else if (timerWasSet) {
      navigate(`/notes/${noteId}`)
    }
  }

  const handlePointerMove = (e: React.PointerEvent) => {
    if (!longPressTimer.current) return
    const dx = e.clientX - pressStartPos.current.x
    const dy = e.clientY - pressStartPos.current.y
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) {
      pointerMoved.current = true
      clearLongPress()
    }
  }

  const toggleNoteSelection = (noteId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(noteId)) {
        next.delete(noteId)
        if (next.size === 0) {
          setSelectMode(false)
        }
      } else {
        next.add(noteId)
      }
      return next
    })
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const handleBatchPin = async () => {
    const selectedNotes = notes.filter((n) => selectedIds.has(n.id))
    const allPinned = selectedNotes.every((n) => n.is_pinned)
    const newPinned = !allPinned
    try {
      await notesApi.batchPin(Array.from(selectedIds), newPinned)
      setNotes((prev) => {
        const updated = prev.map((n) =>
          selectedIds.has(n.id) ? { ...n, is_pinned: newPinned } : n
        )
        updated.sort((a, b) => {
          if (a.is_pinned && !b.is_pinned) return -1
          if (!a.is_pinned && b.is_pinned) return 1
          return 0
        })
        return updated
      })
      exitSelectMode()
    } catch {
      // ignore
    }
  }

  const handleBatchDelete = async () => {
    try {
      await notesApi.batchDelete(Array.from(selectedIds))
      exitSelectMode()
      setPage(1)
      refreshCategories()
      loadNotes(1, true)
    } catch {
      // ignore
    }
  }

  const handleBatchDownload = async () => {
    try {
      const blob = await notesApi.batchDownload(Array.from(selectedIds))
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `notes_${new Date().toISOString().slice(0, 10)}.zip`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      exitSelectMode()
    } catch {
      // ignore
    }
  }

  const handleBatchCategory = async (cat: string) => {
    try {
      await notesApi.batchUpdateCategory(Array.from(selectedIds), cat)
      setCategoryModalOpen(false)
      setCustomCategory('')
      exitSelectMode()
      setPage(1)
      refreshCategories()
      loadNotes(1, true)
    } catch {
      // ignore
    }
  }

  return (
    <div className="max-w-4xl mx-auto py-8 px-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-heading text-xl font-semibold text-[var(--color-text)]">
          {selectMode ? t('note.batch.selected', { count: selectedIds.size }) : t('note.title')}
        </h1>
        {!selectMode && (
          <button
            onClick={() => navigate('/notes/new')}
            className="flex items-center gap-2 px-4 py-2 rounded-md bg-[var(--color-accent)] text-white text-sm hover:bg-blue-700 transition-colors"
          >
            <Plus size={16} />
            {t('note.newNote')}
          </button>
        )}
        {selectMode && (
          <button
            onClick={exitSelectMode}
            className="px-4 py-2 text-sm rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors"
          >
            {t('note.batch.cancel')}
          </button>
        )}
      </div>

      {!selectMode && (
        <>
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
            <button
              onClick={() => setManageOpen(true)}
              className="flex items-center gap-1.5 px-3 py-2 text-xs rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] transition-colors shrink-0"
            >
              <Settings2 size={14} />
              管理分类
            </button>
          </div>

          <div className="flex gap-2 mb-6 flex-wrap">
            {allCategories.map((cat) => (
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
        </>
      )}

      {selectMode && notes.length > 0 && (
        <p className="text-xs text-[var(--color-text-tertiary)] mb-4">{t('note.batch.selectHint')}</p>
      )}

      {selectMode && selectedIds.size > 0 && (
        <BatchActionBar
          selectedCount={selectedIds.size}
          onDelete={() => setDeleteConfirmOpen(true)}
          onDownload={handleBatchDownload}
          onCategory={() => setCategoryModalOpen(true)}
          onPin={handleBatchPin}
          onCancel={exitSelectMode}
        />
      )}

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
          {notes.map((note) => {
            const isSelected = selectedIds.has(note.id)
            return (
              <div
                key={note.id}
                onPointerDown={(e) => handlePointerDown(note.id, e)}
                onPointerUp={() => handlePointerUp(note.id)}
                onPointerMove={handlePointerMove}
                onPointerLeave={clearLongPress}
                className={`px-5 py-4 rounded-lg bg-[var(--color-card)] border cursor-pointer transition-colors ${
                  isSelected
                    ? 'border-[var(--color-accent)] ring-1 ring-[var(--color-accent)]'
                    : 'border-[var(--color-border)] hover:border-[var(--color-accent)]'
                }`}
              >
                <div className="flex items-start gap-3">
                  {selectMode && (
                    <div className="mt-0.5 text-[var(--color-accent)] shrink-0">
                      {isSelected ? <CheckSquare size={18} /> : <Square size={18} className="text-[var(--color-text-tertiary)]" />}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between mb-1">
                      <h3 className="text-sm font-medium text-[var(--color-text)] truncate">{note.title || '无标题'}</h3>
                      <div className="flex items-center gap-1 shrink-0 ml-3">
                        <button
                          onClick={(e) => handlePin(e, note.id)}
                          onPointerDown={(e) => e.stopPropagation()}
                          onPointerUp={(e) => e.stopPropagation()}
                          className="p-0.5 rounded hover:bg-[var(--color-bg-secondary)] transition-colors"
                          title={note.is_pinned ? '取消置顶' : '置顶'}
                        >
                          <Pin
                            size={14}
                            className={note.is_pinned ? 'text-[var(--color-accent)] fill-[var(--color-accent)]' : 'text-[var(--color-text-tertiary)]'}
                          />
                        </button>
                        <span className="text-xs text-[var(--color-text-tertiary)]">{formatDate(note.created_at)}</span>
                      </div>
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
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div ref={sentinelRef} className="h-4" />
      {loading && (
        <div className="flex justify-center py-4">
          <div className="w-5 h-5 border-2 border-[var(--color-border)] border-t-[var(--color-accent)] rounded-full animate-spin" />
        </div>
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        onOpenChange={setDeleteConfirmOpen}
        title={t('note.delete')}
        message={t('note.batch.deleteConfirm', { count: selectedIds.size })}
        variant="danger"
        onConfirm={handleBatchDelete}
      />

      <CategoryModal
        open={categoryModalOpen}
        onOpenChange={(open) => {
          setCategoryModalOpen(open)
          if (!open) setCustomCategory('')
        }}
        categories={allCategories.filter((c) => c.value !== '')}
        customCategory={customCategory}
        onCustomCategoryChange={setCustomCategory}
        onSelect={handleBatchCategory}
        t={t}
      />

      <CategoryManageDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        categories={allCategories.filter((c) => c.value !== '').map((c) => ({
          category: c.value,
          count: categoryCounts[c.value] ?? 0,
        }))}
        onRefresh={() => refreshCategories(extraCategories)}
        onCreateCategory={(name) => setExtraCategories((prev) => [...prev, name])}
      />
    </div>
  )
}

function CategoryModal({
  open,
  onOpenChange,
  categories,
  customCategory,
  onCustomCategoryChange,
  onSelect,
  t,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  categories: { label: string; value: string }[]
  customCategory: string
  onCustomCategoryChange: (v: string) => void
  onSelect: (cat: string) => void
  t: (key: string, opts?: Record<string, unknown>) => string
}) {
  return open ? (
    <>
      <div className="fixed inset-0 bg-black/40 z-50" onClick={() => onOpenChange(false)} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-[var(--color-card)] rounded-lg shadow-xl p-6 w-[400px] max-w-[90vw]">
        <h3 className="text-base font-medium text-[var(--color-text)] mb-4">{t('note.batch.categoryTitle')}</h3>
        <div className="flex flex-wrap gap-2 mb-4">
          {categories.map((cat) => (
            <button
              key={cat.value}
              onClick={() => onSelect(cat.value)}
              className="px-3 py-1.5 text-xs rounded-md bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-accent-bg)] hover:text-[var(--color-accent)] transition-colors"
            >
              {cat.label}
            </button>
          ))}
        </div>
        <div className="border-t border-[var(--color-border)] pt-4">
          <p className="text-xs text-[var(--color-text-tertiary)] mb-2">{t('note.batch.categoryCustom')}</p>
          <div className="flex gap-2">
            <input
              type="text"
              value={customCategory}
              onChange={(e) => onCustomCategoryChange(e.target.value)}
              placeholder={t('note.batch.categoryCustomPlaceholder')}
              className="flex-1 px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
            />
            <button
              onClick={() => { if (customCategory.trim()) onSelect(customCategory.trim()) }}
              disabled={!customCategory.trim()}
              className="px-4 py-1.5 text-sm rounded-md bg-[var(--color-accent)] text-white disabled:opacity-40 transition-opacity"
            >
              {t('common.confirm')}
            </button>
          </div>
        </div>
      </div>
    </>
  ) : null
}
