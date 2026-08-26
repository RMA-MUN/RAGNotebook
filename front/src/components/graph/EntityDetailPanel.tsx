import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { graphApi } from '../../api/graph'
import type { EntityNoteLink, GraphEntity } from '../../types/graph'

interface Props {
  nodeId: string
  nodeType: 'entity' | 'note'
  onClose: () => void
  onChanged: () => void
}

export function EntityDetailPanel({ nodeId, nodeType, onClose, onChanged }: Props) {
  const { t } = useTranslation()
  const [entity, setEntity] = useState<GraphEntity | null>(null)
  const [links, setLinks] = useState<EntityNoteLink[]>([])

  useEffect(() => {
    if (nodeType !== 'entity') return
    void graphApi.entity(nodeId).then((e) => setEntity(e))
    void graphApi.entityNotes(nodeId).then((l) => setLinks(l))
  }, [nodeId, nodeType])

  if (nodeType === 'note') {
    return (
      <div className="fixed right-0 top-0 h-full w-80 border-l bg-[var(--color-card)] p-4">
        <button onClick={onClose} className="float-right">×</button>
        <h2 className="text-lg font-semibold">{t('graph.noteTitle')}</h2>
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{nodeId}</p>
      </div>
    )
  }

  return (
    <div className="fixed right-0 top-0 h-full w-80 overflow-auto border-l bg-[var(--color-card)] p-4">
      <button onClick={onClose} className="float-right">×</button>
      <h2 className="text-lg font-semibold">{entity?.display_name || entity?.name || nodeId}</h2>
      {entity?.description && <p className="mt-2 text-sm">{entity.description}</p>}
      {entity && (
        <div className="mt-4 space-y-2 text-sm">
          <div><span className="font-medium">{t('graph.aliases')}:</span> {entity.aliases.join(', ') || '—'}</div>
          <div><span className="font-medium">{t('graph.type')}:</span> {entity.type_id || '—'}</div>
          <div className="mt-2 flex gap-2">
            <button className="rounded bg-[var(--color-accent)] px-2 py-1 text-white"
              onClick={() => { void graphApi.deleteEntity(entity.id); onChanged() }}>
              {t('graph.delete')}
            </button>
          </div>
        </div>
      )}
      <div className="mt-4">
        <h3 className="font-medium">{t('graph.relatedNotes')}</h3>
        {links.map((l) => (
          <div key={l.note_id} className="mt-1 text-sm text-[var(--color-accent)]">
            {l.note_id}
            {l.context[0] && <p className="text-xs text-[var(--color-text-secondary)]">“{l.context[0].snippet}”</p>}
          </div>
        ))}
      </div>
    </div>
  )
}