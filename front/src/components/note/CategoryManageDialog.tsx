import { useState, useRef, useEffect } from 'react'
import { Trash2, Plus, FolderTree, X, GripVertical } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { notesApi } from '../../api/notes'
import ConfirmDialog from '../common/ConfirmDialog'

const STORAGE_KEY = 'note_category_order'

interface CategoryItem {
  category: string
  count: number
}

interface CategoryManageDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  categories: CategoryItem[]
  onRefresh: () => void
  onCreateCategory: (name: string) => void
}

const PREDEFINED_VALUES = new Set(['work', 'study', 'life', 'project', 'other'])

const CATEGORY_LABEL_MAP: Record<string, string> = {
  work: '工作',
  study: '学习',
  life: '生活',
  project: '技术',
  other: '其他',
}

function getLabel(cat: string) {
  return CATEGORY_LABEL_MAP[cat] || cat
}

function saveOrder(items: CategoryItem[]) {
  const order = items.map((c) => c.category)
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(order))
  } catch {
    // ignore
  }
}

function getSavedOrder(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export default function CategoryManageDialog({
  open,
  onOpenChange,
  categories,
  onRefresh,
  onCreateCategory,
}: CategoryManageDialogProps) {
  const { t } = useTranslation()
  const [deleteTarget, setDeleteTarget] = useState<CategoryItem | null>(null)
  const [newCategory, setNewCategory] = useState('')
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)
  const [items, setItems] = useState<CategoryItem[]>([])
  const dragItem = useRef<number | null>(null)

  useEffect(() => {
    setItems([...categories])
  }, [open])

  if (!open) return null

  const handleDelete = async () => {
    const target = deleteTarget
    if (!target) return
    try {
      await notesApi.deleteCategory(target.category)
      setDeleteTarget(null)
      setItems((prev) => prev.filter((item) => item.category !== target.category))
      onRefresh()
    } catch {
      // ignore
    }
  }

  const handleCreate = () => {
    const name = newCategory.trim()
    if (!name) return
    if (categories.some((c) => c.category === name)) return
    onCreateCategory(name)
    setItems((prev) => [...prev, { category: name, count: 0 }])
    setNewCategory('')
  }

  const handleDragStart = (index: number) => {
    dragItem.current = index
  }

  const handleDragOver = (index: number) => {
    setDragOverIndex(index)
  }

  const handleDrop = (index: number) => {
    const from = dragItem.current
    dragItem.current = null
    setDragOverIndex(null)
    if (from === null || from === index) return
    const reordered = [...items]
    const [moved] = reordered.splice(from, 1)
    reordered.splice(index, 0, moved)
    setItems(reordered)
    saveOrder(reordered)
    onRefresh()
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-50" onClick={() => onOpenChange(false)} />
      <div className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50 bg-[var(--color-card)] rounded-lg shadow-xl p-6 w-[440px] max-w-[90vw] max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-medium text-[var(--color-text)]">{t('note.category')}管理</h3>
          <button
            onClick={() => onOpenChange(false)}
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text)]"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex gap-2 mb-4">
          <input
            type="text"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleCreate() }}
            placeholder="输入新分类名称"
            className="flex-1 px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
          />
          <button
            onClick={handleCreate}
            disabled={!newCategory.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md bg-[var(--color-accent)] text-white disabled:opacity-40 transition-opacity"
          >
            <Plus size={14} />
            新建
          </button>
        </div>

        <div className="flex-1 overflow-y-auto space-y-1">
          {items.map((item, index) => {
            const isPredefined = PREDEFINED_VALUES.has(item.category)
            return (
              <div
                key={item.category}
                draggable
                onDragStart={() => handleDragStart(index)}
                onDragOver={(e) => { e.preventDefault(); handleDragOver(index) }}
                onDrop={(e) => { e.preventDefault(); handleDrop(index) }}
                onDragEnd={() => setDragOverIndex(null)}
                className={`flex items-center justify-between px-3 py-2 rounded-md transition-colors group cursor-default ${
                  dragOverIndex === index
                    ? 'border-t-2 border-t-[var(--color-accent)] bg-[var(--color-bg-secondary)]'
                    : 'hover:bg-[var(--color-bg-secondary)]'
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <GripVertical size={14} className="text-[var(--color-text-tertiary)] shrink-0 cursor-grab active:cursor-grabbing" />
                  <FolderTree size={14} className="text-[var(--color-text-tertiary)] shrink-0" />
                  <span className="text-sm text-[var(--color-text)] truncate">{getLabel(item.category)}</span>
                  <span className="text-xs text-[var(--color-text-tertiary)]">({item.count})</span>
                </div>
                {!isPredefined && (
                  <button
                    onClick={() => setDeleteTarget(item)}
                    className="p-1 rounded text-[var(--color-text-tertiary)] opacity-0 group-hover:opacity-100 hover:text-red-500 hover:bg-red-500/10 transition-all"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            )
          })}
          {items.length === 0 && (
            <p className="text-xs text-[var(--color-text-tertiary)] text-center py-8">暂无分类</p>
          )}
        </div>

        <p className="text-xs text-[var(--color-text-tertiary)] mt-3 text-center">
          预设分类不可删除
        </p>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title="删除分类"
        message={`确定要删除分类「${deleteTarget ? getLabel(deleteTarget.category) : ''}」吗？这将同步删除该分类下的 ${deleteTarget?.count ?? 0} 篇笔记。`}
        variant="danger"
        confirmText="删除"
        onConfirm={handleDelete}
      />
    </>
  )
}
