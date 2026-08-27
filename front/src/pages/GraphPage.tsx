import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { FileText, Network } from 'lucide-react'
import { graphApi } from '../api/graph'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { EntityDetailPanel } from '../components/graph/EntityDetailPanel'
import EmptyState from '../components/common/EmptyState'
import { useGraphEvents } from '../hooks/useGraphEvents'
import { useDebounce } from '../hooks/useDebounce'
import type { EntityType, GraphSSEEvent, GraphSearchResult, GraphView } from '../types/graph'

// 后端 /overview 的 limit 上限即 200
const GRAPH_OVERVIEW_LIMIT = 200
const SSE_RELOAD_DEBOUNCE_MS = 1500

export default function GraphPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [view, setView] = useState<GraphView>({ nodes: [], edges: [] })
  const [types, setTypes] = useState<EntityType[]>([])
  const [q, setQ] = useState('')
  const [activeType, setActiveType] = useState<string>('')
  const [selected, setSelected] = useState<{ id: string; nodeType: 'entity' | 'note' } | null>(null)
  const [fitSignal, setFitSignal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [searchParams] = useSearchParams()

  // 双链/实体链接携带 ?entity=<id> 进入图谱页时，直接选中该实体
  const entityParam = searchParams.get('entity')
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 路由参数驱动的一次性选中
    if (entityParam) setSelected({ id: entityParam, nodeType: 'entity' })
  }, [entityParam])

  const reload = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    try {
      const [v, ty] = await Promise.all([
        graphApi.overview({ types: activeType || undefined, limit: GRAPH_OVERVIEW_LIMIT }),
        graphApi.types(),
      ])
      setView(v)
      setTypes(ty)
      setFailed(false)
    } catch (err: unknown) {
      console.warn('[graph] 加载图谱失败:', err)
      setFailed(true)
      if (!silent) toast.error(t('graph.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [activeType, t])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- 页面挂载/筛选变化时拉取一次图谱数据
  useEffect(() => { void reload() }, [reload])

  // 抽取事件用防抖合并：批量导入笔记时不会连环整拉重渲染
  const sseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (sseTimerRef.current) clearTimeout(sseTimerRef.current)
  }, [])
  const onEvent = useCallback((ev: GraphSSEEvent) => {
    if (ev.type !== 'extract_done' && ev.type !== 'extract_failed') return
    if (sseTimerRef.current) clearTimeout(sseTimerRef.current)
    sseTimerRef.current = setTimeout(() => {
      sseTimerRef.current = null
      void reload({ silent: true }) // 整拉简化，避免过期视图；后台刷新失败不弹打扰性提示
    }, SSE_RELOAD_DEBOUNCE_MS)
  }, [reload])

  const { subscribe, connected } = useGraphEvents(onEvent)
  useEffect(() => { subscribe() }, [subscribe])

  // ---- 搜索：防抖请求 + 下拉候选，点实体选中、点笔记跳转 ----
  const debouncedQ = useDebounce(q.trim(), 300)
  const [searchResults, setSearchResults] = useState<GraphSearchResult | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const searchBoxRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!debouncedQ) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 输入清空时收起候选浮层
      setSearchOpen(false)
      return
    }
    let latest = true
    void (async () => {
      try {
        const res = await graphApi.search(debouncedQ)
        if (!latest) return
        setSearchResults(res ?? { entities: [], notes: [] })
        setSearchOpen(true)
      } catch { /* 搜索失败不打扰用户 */
      }
    })()
    return () => { latest = false }
  }, [debouncedQ])

  useEffect(() => {
    if (!searchOpen) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!searchBoxRef.current?.contains(e.target as Node)) setSearchOpen(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [searchOpen])

  const closeSearch = useCallback(() => {
    setQ('')
    setSearchResults(null)
    setSearchOpen(false)
  }, [])

  const selectEntityFromSearch = useCallback((id: string) => {
    setSelected({ id, nodeType: 'entity' })
    closeSearch()
  }, [closeSearch])

  const openNoteFromSearch = useCallback((id: string) => {
    navigate(`/notes/${id}`)
    closeSearch()
  }, [navigate, closeSearch])

  // 回车定位第一个命中结果
  const onSearchEnter = useCallback(() => {
    const firstEntity = searchResults?.entities[0]
    if (firstEntity) {
      selectEntityFromSearch(firstEntity.id)
      return
    }
    const firstNote = searchResults?.notes[0]
    if (firstNote) openNoteFromSearch(firstNote.id)
  }, [searchResults, selectEntityFromSearch, openNoteFromSearch])

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

  const showEmptyOverlay = !loading && view.nodes.length === 0

  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded bg-[var(--color-card)] p-2 shadow">
        <span aria-label={connected ? t('graph.sseConnected') : t('graph.sseReconnecting')}
          title={connected ? t('graph.sseConnected') : t('graph.sseReconnecting')}
          className={`inline-block h-2 w-2 shrink-0 rounded-full ${connected ? 'bg-emerald-500' : 'bg-red-400'}`} />
        <div ref={searchBoxRef} className="relative">
          <input value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onSearchEnter()
              if (e.key === 'Escape') closeSearch()
            }}
            placeholder={t('graph.searchPlaceholder')}
            className="rounded border px-2 py-1 text-sm" />
          {searchOpen && searchResults && (
            <div className="absolute left-0 top-full z-20 mt-1 max-h-96 w-64 overflow-auto rounded border bg-[var(--color-card)] py-1 shadow-lg">
              {searchResults.entities.length === 0 && searchResults.notes.length === 0 && (
                <p className="px-3 py-2 text-sm text-[var(--color-text-secondary)]">{t('graph.searchNoResult')}</p>
              )}
              {searchResults.entities.length > 0 && (
                <>
                  <p className="px-3 pt-1 pb-0.5 text-xs text-[var(--color-text-tertiary)]">{t('graph.entitiesGroup')}</p>
                  {searchResults.entities.map((en) => (
                    <button key={en.id} onClick={() => selectEntityFromSearch(en.id)}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--color-bg-secondary)]">
                      <span className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: (en.type_id && typeColors[en.type_id]) || '#64748b' }} />
                      <span className="truncate">{en.name}</span>
                    </button>
                  ))}
                </>
              )}
              {searchResults.notes.length > 0 && (
                <>
                  <p className="px-3 pt-1 pb-0.5 text-xs text-[var(--color-text-tertiary)]">{t('graph.notesGroup')}</p>
                  {searchResults.notes.map((n) => (
                    <button key={n.id} onClick={() => openNoteFromSearch(n.id)}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--color-bg-secondary)]">
                      <FileText size={13} className="shrink-0" />
                      <span className="truncate">{n.title || n.id}</span>
                    </button>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
        <select value={activeType} onChange={(e) => setActiveType(e.target.value)} className="rounded border px-2 py-1 text-sm">
          <option value="">{t('graph.allTypes')}</option>
          {types.map((ty) => <option key={ty.id} value={ty.id}>{ty.display_name}</option>)}
        </select>
        <button onClick={() => { setFitSignal((s) => s + 1); void reload() }}
          className="rounded bg-[var(--color-accent)] px-2 py-1 text-sm text-white">{t('graph.refresh')}</button>
      </div>

      <GraphCanvas view={view} typeColors={typeColors} onSelectNode={onSelectNode}
        fitSignal={fitSignal} />

      {failed && view.nodes.length > 0 && (
        <div className="absolute right-3 bottom-3 z-10 rounded bg-[var(--color-card)] px-3 py-2 text-xs text-red-500 shadow">
          {t('graph.loadFailed')}
        </div>
      )}
      {showEmptyOverlay && (
        <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center">
          <div className="pointer-events-auto">
            <EmptyState icon={<Network size={48} />} message={t(failed ? 'graph.loadFailed' : 'graph.emptyMessage')}
              action={<button onClick={() => { setLoading(true); void reload() }}
                className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-sm text-white">{t('graph.retry')}</button>} />
          </div>
        </div>
      )}
      {!failed && view.nodes.length >= GRAPH_OVERVIEW_LIMIT && activeType === '' && (
        <div className="absolute right-3 top-3 z-10 rounded bg-[var(--color-card)] px-2 py-1 text-xs text-[var(--color-text-secondary)] shadow">
          {t('graph.truncatedHint', { count: GRAPH_OVERVIEW_LIMIT })}
        </div>
      )}

      {selected && (
        <EntityDetailPanel key={`${selected.nodeType}:${selected.id}`}
          nodeId={selected.id} nodeType={selected.nodeType} types={types}
          onClose={() => setSelected(null)} onChanged={() => void reload()} />
      )}
    </div>
  )
}
