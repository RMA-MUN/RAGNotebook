import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Heading1, Heading2, Heading3 } from 'lucide-react'

interface HeadingItem {
  level: number
  text: string
}

interface Props {
  content: string
  open: boolean
  onClose: () => void
  onHeadingClick: (text: string, level: number) => void
}

function stripInlineFormatting(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`[^`]*`/g, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    .replace(/#+\s*$/, '')
    .trim()
}

function parseHeadings(md: string): HeadingItem[] {
  const noCode = md.replace(/```[\s\S]*?```/g, '').replace(/`{1,3}[^`]*`{1,3}/g, '')
  const regex = /^(#{1,6})\s+(.+)$/gm
  const items: HeadingItem[] = []
  let match: RegExpExecArray | null
  while ((match = regex.exec(noCode)) !== null) {
    const text = stripInlineFormatting(match[2])
    if (text) {
      items.push({ level: match[1].length, text })
    }
  }
  return items
}

const levelIcon = (level: number) => {
  switch (level) {
    case 1: return <Heading1 size={14} />
    case 2: return <Heading2 size={14} />
    case 3: return <Heading3 size={14} />
    default: return <span className="text-xs font-bold">H{level}</span>
  }
}

export default function OutlinePanel({ content, open, onClose, onHeadingClick }: Props) {
  const { t } = useTranslation()
  const headings = useMemo(() => parseHeadings(content), [content])

  if (!open) return null

  return (
    <div className="w-60 flex flex-col border-r border-[var(--color-border)] bg-[var(--color-card)] shrink-0">
      <div className="flex items-center justify-between px-4 h-12 border-b border-[var(--color-border-light)]">
        <h2 className="text-sm font-medium text-[var(--color-text)]">
          {t('note.outline')}
          <span className="ml-1.5 text-xs text-[var(--color-text-tertiary)]">({headings.length})</span>
        </h2>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {headings.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-[var(--color-text-tertiary)]">
            暂无标题
          </div>
        ) : (
          <div className="p-2 space-y-0.5">
            {headings.map((h, i) => (
              <button
                key={`${h.text}-${i}`}
                onClick={() => onHeadingClick(h.text, h.level)}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-md text-xs text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] hover:text-[var(--color-text)] transition-colors"
                style={{ paddingLeft: `${8 + (h.level - 1) * 16}px` }}
              >
                <span className="shrink-0 text-[var(--color-text-tertiary)]">
                  {levelIcon(h.level)}
                </span>
                <span className="truncate">{h.text}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
