export default function LoadingSkeleton() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="space-y-4 w-full max-w-md px-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="animate-pulse space-y-2">
            <div className="h-4 bg-[var(--color-bg-tertiary)] rounded w-3/4" />
            <div className="h-3 bg-[var(--color-bg-tertiary)] rounded w-full" />
            <div className="h-3 bg-[var(--color-bg-tertiary)] rounded w-5/6" />
          </div>
        ))}
      </div>
    </div>
  )
}
