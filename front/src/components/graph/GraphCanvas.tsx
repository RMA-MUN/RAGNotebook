import { useEffect, useRef } from 'react'
import { Graph } from '@antv/g6'
import type { IElementEvent } from '@antv/g6'
import type { GraphView } from '../../types/graph'

interface Props {
  view: GraphView
  typeColors: Record<string, string>
  onSelectNode?: (nodeId: string) => void
}

export function GraphCanvas({ view, typeColors, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<Graph | null>(null)
  const onSelectNodeRef = useRef(onSelectNode)
  useEffect(() => {
    onSelectNodeRef.current = onSelectNode
  })

  // 图实例只创建/销毁一次；数据更新走 setData，避免每次 view 变化销毁重建（丢失相机/缩放并产生 destroyed 告警）
  useEffect(() => {
    if (!containerRef.current) return
    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      node: { style: { lineWidth: 1, stroke: '#ffffff' } },
      edge: { style: { endArrow: true } },
      layout: { type: 'force', preventOverlap: true },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
    })
    graphRef.current = graph
    graph.on('node:click', (evt: IElementEvent) => {
      const id = (evt.target as unknown as { id?: string })?.id
      if (id) onSelectNodeRef.current?.(String(id))
    })
    return () => {
      graph.destroy()
      graphRef.current = null
    }
  }, [])

  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    const nodeIds = new Set(view.nodes.map((n) => n.id))
    graph.setData({
      nodes: view.nodes.map((n) => ({
        id: n.id,
        type: n.node_type === 'note' ? 'rect' : 'circle',
        data: { label: n.label, node_type: n.node_type, entity_type_id: n.entity_type_id },
        style: {
          size: n.node_type === 'note' ? 16 : 24,
          fill: n.node_type === 'note'
            ? '#94a3b8'
            : (n.entity_type_id && typeColors[n.entity_type_id]) || '#64748b',
          labelText: n.label,
          labelPlacement: 'bottom',
        },
      })),
      // 防御：丢弃端点不在节点集中的边（后端偶发悬空边不应炸掉整页）
      edges: view.edges
        .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          style: {
            stroke: '#94a3b8',
            lineWidth: 1,
            labelText: e.relation_type || undefined,
            labelFontSize: 10,
            endArrow: true,
          },
        })),
    })
    void graph.render().catch(() => {
      /* 卸载竞态：render 在 destroy 后完成时静默 */
    })
  }, [view, typeColors])

  return <div ref={containerRef} className="h-full w-full" />
}