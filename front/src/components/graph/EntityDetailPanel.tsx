import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { X } from 'lucide-react'
import { graphApi } from '../../api/graph'
import ConfirmDialog from '../common/ConfirmDialog'
import type { EntityNoteLink, EntityType, GraphEntity, GraphView } from '../../types/graph'

interface Props {
  nodeId: string
  nodeType: 'entity' | 'note' | 'doc'
  types?: EntityType[]
  onClose: () => void
  onChanged: () => void
}

export function EntityDetailPanel({ nodeId, nodeType, types = [], onClose, onChanged }: Props) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [entity, setEntity] = useState<GraphEntity | null>(null)
  const [links, setLinks] = useState<EntityNoteLink[]>([])
  const [docView, setDocView] = useState<GraphView | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // 挂载时拉取一次（父组件以 key=nodeType:id 重挂载本组件来切换目标，
  // 因此无需在 nodeId 变化时清空旧数据；latest 守卫拦下卸载后的迟到响应）
  useEffect(() => {
    if (nodeType !== 'entity') return
    let latest = true
    void (async () => {
      try {
        const [e, ls] = await Promise.all([graphApi.entity(nodeId), graphApi.entityNotes(nodeId)])
        if (!latest) return
        setEntity(e)
        setLinks(ls ?? [])
      } catch {
        if (latest) toast.error(t('graph.loadEntityFailed'))
      }
    })()
    return () => { latest = false }
  }, [nodeId, nodeType, t])

  // 文档节点：拉取文档子图（文档 + 关联实体）
  useEffect(() => {
    if (nodeType !== 'doc') return
    let latest = true
    void (async () => {
      try {
        const v = await graphApi.docRelated(nodeId)
        if (!latest) return
        setDocView(v)
      } catch {
        if (latest) toast.error(t('graph.loadDocFailed'))
      }
    })()
    return () => { latest = false }
  }, [nodeId, nodeType, t])

  const handleDelete = async () => {
    if (!entity) return
    try {
      await graphApi.deleteEntity(entity.id)
      toast.success(t('graph.deleteSuccess'))
      onClose()
      onChanged()
    } catch {
      toast.error(t('graph.deleteFailed'))
    }
  }

  const closeBtn = (
    <button onClick={onClose} aria-label={t('graph.close')}
      className="float-right p-1 text-[var(--color-text-tertiary)] hover:text-[var(--color-text)]">
      <X size={16} />
    </button>
  )

  if (nodeType === 'note') {
    return (
      <div className="fixed right-0 top-0 h-full w-80 border-l bg-[var(--color-card)] p-4">
        {closeBtn}
        <h2 className="text-lg font-semibold">{t('graph.noteTitle')}</h2>
        <p className="mt-2 break-all text-sm text-[var(--color-text-secondary)]">{nodeId}</p>
        <button className="mt-4 rounded bg-[var(--color-accent)] px-2 py-1 text-sm text-white"
          onClick={() => navigate(`/notes/${nodeId}`)}>
          {t('graph.openNote')}
        </button>
      </div>
    )
  }

  if (nodeType === 'doc') {
    const docLabel = docView?.nodes.find((n) => n.node_type === 'doc')?.label || nodeId
    const docEntities = docView?.nodes.filter((n) => n.node_type === 'entity') ?? []
    return (
      <div className="fixed right-0 top-0 h-full w-80 overflow-auto border-l bg-[var(--color-card)] p-4">
        {closeBtn}
        <h2 className="break-all text-lg font-semibold">{docLabel}</h2>
        <p className="mt-2 break-all text-xs text-[var(--color-text-secondary)]">{nodeId}</p>
        <div className="mt-4">
          <h3 className="font-medium">{t('graph.relatedEntities')}</h3>
          {docEntities.length === 0 && (
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t('graph.noRelatedEntities')}</p>
          )}
          <ul className="mt-2 space-y-1">
            {docEntities.map((en) => (
              <li key={en.id} className="text-sm text-[var(--color-text)]">{en.label}</li>
            ))}
          </ul>
        </div>
        <button className="mt-4 rounded bg-[var(--color-accent)] px-2 py-1 text-sm text-white"
          onClick={() => navigate('/knowledge')}>
          {t('graph.openDoc')}
        </button>
      </div>
    )
  }

  const typeName = types.find((ty) => ty.id === entity?.type_id)?.display_name || entity?.type_id

  return (
    <div className="fixed right-0 top-0 h-full w-80 overflow-auto border-l bg-[var(--color-card)] p-4">
      {closeBtn}
      <h2 className="text-lg font-semibold">{entity?.display_name || entity?.name || nodeId}</h2>
      {entity?.description && <p className="mt-2 text-sm">{entity.description}</p>}
      {entity && (
        <div className="mt-4 space-y-2 text-sm">
          <div><span className="font-medium">{t('graph.aliases')}:</span> {(entity.aliases ?? []).join(', ') || '—'}</div>
          <div><span className="font-medium">{t('graph.type')}:</span> {typeName || '—'}</div>
          <div className="mt-2 flex gap-2">
            <button className="rounded bg-[var(--color-danger)] px-2 py-1 text-white"
              onClick={() => setConfirmDelete(true)}>
              {t('graph.delete')}
            </button>
          </div>
        </div>
      )}
      <div className="mt-4">
        <h3 className="font-medium">{t('graph.relatedNotes')}</h3>
        {links.length === 0 && <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t('graph.noRelatedNotes')}</p>}
        {links.map((l) => (
          <div key={l.note_id} className="mt-2">
            {l.source_type === 'doc' ? (
              <button className="break-all text-sm text-[var(--color-text-secondary)] hover:underline"
                onClick={() => navigate('/knowledge')}>
                {l.source_name || l.note_id}
              </button>
            ) : (
              <button className="break-all text-sm text-[var(--color-accent)] hover:underline"
                onClick={() => navigate(`/notes/${l.note_id}`)}>
                {l.note_id}
              </button>
            )}
            {l.context[0] && <p className="text-xs text-[var(--color-text-secondary)]">“{l.context[0].snippet}”</p>}
          </div>
        ))}
      </div>
      <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete}
        title={t('graph.deleteConfirmTitle')}
        message={t('graph.deleteConfirmMessage', { name: entity?.display_name || entity?.name || nodeId })}
        confirmText={t('graph.delete')} variant="danger"
        onConfirm={() => { void handleDelete() }} />
    </div>
  )
}
