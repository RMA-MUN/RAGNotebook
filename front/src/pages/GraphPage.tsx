import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { graphApi } from '../api/graph'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { EntityDetailPanel } from '../components/graph/EntityDetailPanel'
import { useGraphEvents } from '../hooks/useGraphEvents'
import type { EntityType, GraphSSEEvent, GraphView } from '../types/graph'

export default function GraphPage() {
  const { t } = useTranslation()
  const [view, setView] = useState<GraphView>({ nodes: [], edges: [] })
  const [types, setTypes] = useState<EntityType[]>([])
  const [q, setQ] = useState('')
  const [activeType, setActiveType] = useState<string>('')
  const [selected, setSelected] = useState<{ id: string; nodeType: 'entity' | 'note' } | null>(null)
  const [layout, setLayout] = useState<'force' | 'radial'>('force')
  const [searchParams] = useSearchParams()

  // 双链/实体链接携带 ?entity=<id> 进入图谱页时，直接选中该实体
  const entityParam = searchParams.get('entity')
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 路由参数驱动的一次性选中
    if (entityParam) setSelected({ id: entityParam, nodeType: 'entity' })
  }, [entityParam])

  const reload = useCallback(async () => {
    const [v, ty] = await Promise.all([
      graphApi.overview({ types: activeType || undefined, limit: 100 }),
      graphApi.types(),
    ])
    setView(v)
    setTypes(ty)
  }, [activeType])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- 页面挂载时拉取一次图谱数据
  useEffect(() => { void reload() }, [reload, layout])

  const onEvent = useCallback((ev: GraphSSEEvent) => {
    if (ev.type === 'extract_done' || ev.type === 'extract_failed') {
      void reload() // 增量刷新（整拉简化，避免过期视图）
    }
  }, [reload])

  const { subscribe } = useGraphEvents(onEvent)
  useEffect(() => { subscribe() }, [subscribe])

  const onSearch = useCallback(async () => {
    if (!q.trim()) return
    await graphApi.search(q.trim())
    setView({ nodes: [], edges: [] }) // 搜索态简化：本期仅定位，可由详情面板承接
    void reload()
  }, [q, reload])

  const typeColors = useMemo(
    () => Object.fromEntries(types.map((ty) => [ty.id, ty.color])),
    [types],
  )

  const onSelectNode = useCallback((id: string) => {
    setSelected((prev) => {
      const n = view.nodes.find((x) => x.id === id)
      return n ? { id: n.id, nodeType: n.node_type } : prev
    })
  }, [view])

  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded bg-[var(--color-card)] p-2 shadow">
        <input value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearch()}
          placeholder={t('graph.searchPlaceholder')}
          className="rounded border px-2 py-1 text-sm" />
        <select value={activeType} onChange={(e) => setActiveType(e.target.value)} className="rounded border px-2 py-1 text-sm">
          <option value="">{t('graph.allTypes')}</option>
          {types.map((ty) => <option key={ty.id} value={ty.id}>{ty.display_name}</option>)}
        </select>
        <select value={layout} onChange={(e) => setLayout(e.target.value as 'force' | 'radial')} className="rounded border px-2 py-1 text-sm">
          <option value="force">{t('graph.layoutForce')}</option>
          <option value="radial">{t('graph.layoutRadial')}</option>
        </select>
        <button onClick={() => void reload()} className="rounded bg-[var(--color-accent)] px-2 py-1 text-sm text-white">{t('graph.refresh')}</button>
      </div>
      <GraphCanvas view={view} typeColors={typeColors} onSelectNode={onSelectNode} />
      {selected && (
        <EntityDetailPanel nodeId={selected.id} nodeType={selected.nodeType}
          onClose={() => setSelected(null)} onChanged={() => void reload()} />
      )}
    </div>
  )
}