import { useEffect, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { useTranslation } from 'react-i18next'
import { X, Loader2, AlertCircle, FileText } from 'lucide-react'
import { knowledgeApi } from '../../api/knowledge'
import type { KnowledgeDocumentDetail } from '../../types/api'
import AuthImage from '../common/AuthImage'

interface DocumentDetailDrawerProps {
  filename: string | null
  onClose: () => void
}

type Tab = 'content' | 'chunks'

export default function DocumentDetailDrawer({ filename, onClose }: DocumentDetailDrawerProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('content')
  const [detail, setDetail] = useState<KnowledgeDocumentDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!filename) {
      setDetail(null)
      return
    }
    setLoading(true)
    setError(false)
    setTab('content')
    knowledgeApi.detail(filename)
      .then((res) => setDetail(res.data))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [filename])

  return (
    <Dialog.Root open={!!filename} onOpenChange={(open) => { if (!open) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/40" />
        <Dialog.Content className="fixed top-0 right-0 h-full w-[640px] max-w-[90vw] bg-[var(--color-card)] shadow-xl flex flex-col">
          <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
            <Dialog.Title className="text-base font-medium text-[var(--color-text)] truncate flex items-center gap-2">
              <FileText size={16} className="text-[var(--color-text-tertiary)] shrink-0" />
              {detail?.filename || filename || ''}
            </Dialog.Title>
            <Dialog.Close className="p-1.5 rounded text-[var(--color-text-tertiary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-secondary)] transition-colors">
              <X size={18} />
            </Dialog.Close>
          </div>

          <div className="flex border-b border-[var(--color-border)] px-6 shrink-0">
            <button
              onClick={() => setTab('content')}
              className="px-4 py-3 text-sm border-b-2 transition-colors"
              style={{
                borderColor: tab === 'content' ? 'var(--color-accent)' : 'transparent',
                color: tab === 'content' ? 'var(--color-accent)' : 'var(--color-text-secondary)',
              }}
            >
              {t('knowledge.detail')}
            </button>
            <button
              onClick={() => setTab('chunks')}
              className="px-4 py-3 text-sm border-b-2 transition-colors"
              style={{
                borderColor: tab === 'chunks' ? 'var(--color-accent)' : 'transparent',
                color: tab === 'chunks' ? 'var(--color-accent)' : 'var(--color-text-secondary)',
              }}
            >
              {t('knowledge.chunks')} ({detail?.chunks?.length || 0})
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-6">
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <Loader2 size={24} className="animate-spin text-[var(--color-text-tertiary)]" />
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center py-20 text-[var(--color-text-tertiary)] gap-2">
                <AlertCircle size={20} />
                <span className="text-sm">{t('common.error')}</span>
              </div>
            ) : !detail ? null : tab === 'content' ? (
              <div className="space-y-6">
                {detail.content && (
                  <pre className="text-sm text-[var(--color-text)] whitespace-pre-wrap font-sans leading-relaxed">
                    {detail.content}
                  </pre>
                )}
                {detail.images.length > 0 && (
                  <div className="space-y-4">
                    {detail.images.map((img, i) => (
                      <AuthImage
                        key={i}
                        src={img}
                        alt={`Document image ${i + 1}`}
                        className="w-full rounded-lg border border-[var(--color-border)]"
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-4">
                {detail.chunks.map((chunk) => (
                  <div
                    key={chunk.chunk_id}
                    className="p-4 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border)]"
                  >
                    <div className="flex items-center gap-2 mb-2 text-xs text-[var(--color-text-tertiary)]">
                      <span className="font-medium text-[var(--color-text-secondary)]">#{chunk.index + 1}</span>
                      {chunk.page > 0 && <span>| {t('knowledge.page')} {chunk.page}</span>}
                    </div>
                    <pre className="text-sm text-[var(--color-text)] whitespace-pre-wrap font-sans leading-relaxed">
                      {chunk.content}
                    </pre>
                    {chunk.images.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {chunk.images.map((img, i) => (
                          <AuthImage
                            key={i}
                            src={img}
                            alt={`Chunk ${chunk.index + 1} image ${i + 1}`}
                            className="w-full rounded-lg border border-[var(--color-border)]"
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
