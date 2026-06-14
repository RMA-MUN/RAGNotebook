import { Trash2, Download, FolderTree, X, Pin } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface BatchActionBarProps {
  selectedCount: number
  onDelete: () => void
  onDownload: () => void
  onCategory: () => void
  onPin: () => void
  onCancel: () => void
}

export default function BatchActionBar({
  selectedCount,
  onDelete,
  onDownload,
  onCategory,
  onPin,
  onCancel,
}: BatchActionBarProps) {
  const { t } = useTranslation()

  return (
    <div className="flex items-center gap-3 px-5 py-3 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)] mb-3">
      <button
        onClick={onCancel}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors"
      >
        <X size={14} />
        {t('note.batch.cancel')}
      </button>

      <div className="flex-1 text-center">
        <span className="text-sm font-medium text-[var(--color-accent)]">
          {t('note.batch.selected', { count: selectedCount })}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={onPin}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text)] transition-colors"
        >
          <Pin size={14} />
          {'置顶'}
        </button>
        <button
          onClick={onCategory}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text)] transition-colors"
        >
          <FolderTree size={14} />
          {t('note.category')}
        </button>
        <button
          onClick={onDownload}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text)] transition-colors"
        >
          <Download size={14} />
          {t('note.download')}
        </button>
        <button
          onClick={onDelete}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-red-500/10 text-red-500 hover:bg-red-500/20 transition-colors"
        >
          <Trash2 size={14} />
          {t('note.delete')}
        </button>
      </div>
    </div>
  )
}
