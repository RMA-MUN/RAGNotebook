import { useRef, useState } from 'react'
import { X } from 'lucide-react'

interface TagInputProps {
  tags: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
}

export default function TagInput({ tags, onChange, placeholder }: TagInputProps) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  function addTag(raw: string) {
    const tag = raw.trim()
    if (!tag) return
    if (!tags.includes(tag)) {
      onChange([...tags, tag])
    }
    setValue('')
  }

  function removeTag(index: number) {
    onChange(tags.filter((_, i) => i !== index))
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(value)
    } else if (e.key === 'Backspace' && !value && tags.length > 0) {
      removeTag(tags.length - 1)
    }
  }

  function handlePaste(e: React.ClipboardEvent) {
    const text = e.clipboardData.getData('text')
    if (!/[，,]/.test(text)) return
    e.preventDefault()
    const parts = text.split(/[，,]/).map((s) => s.trim()).filter(Boolean)
    const merged = [...tags]
    for (const p of parts) {
      if (p && !merged.includes(p)) merged.push(p)
    }
    onChange(merged)
  }

  return (
    <div
      className="flex flex-wrap items-center gap-1.5 px-2.5 py-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] cursor-text focus-within:border-[var(--color-accent)] focus-within:ring-1 focus-within:ring-[var(--color-accent)] transition-all min-h-[28px]"
      onClick={() => inputRef.current?.focus()}
    >
      {tags.map((tag, i) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-[var(--color-accent-bg)] text-[var(--color-accent)]"
        >
          {tag}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); removeTag(i) }}
            className="flex items-center justify-center hover:text-[var(--color-danger)] transition-colors"
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        placeholder={tags.length === 0 ? (placeholder || '添加标签...') : ''}
        className="flex-1 min-w-[80px] border-none outline-none bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)]"
      />
    </div>
  )
}
