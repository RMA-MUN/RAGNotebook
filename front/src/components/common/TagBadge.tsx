interface TagBadgeProps {
  tag: string
  color?: string
  size?: 'sm' | 'md'
}

export default function TagBadge({ tag, color, size = 'sm' }: TagBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full font-medium ${
        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm'
      }`}
      style={{
        backgroundColor: color ? `${color}20` : 'var(--color-accent-bg)',
        color: color || 'var(--color-accent)',
      }}
    >
      {tag}
    </span>
  )
}
