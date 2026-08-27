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

  useEffect(() => {
    if (!containerRef.current) return
    let destroyed = false
    const graph = new Graph({
      container: containerRef.current,
      autoFit: 'view',
      data: {
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
        edges: view.edges.map((e) => ({
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
      },
      node: { style: { lineWidth: 1, stroke: '#ffffff' } },
      edge: { style: { endArrow: true } },
      layout: { type: 'force', preventOverlap: true },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
    })
    graphRef.current = graph
    graph.on('node:click', (evt: IElementEvent) => {
      const id = (evt.target as unknown as { id?: string })?.id
      if (id) onSelectNode?.(String(id))
    })
    // render 为异步：组件卸载/重建时 pending render 会以 rejected promise 结束，需吞掉（destroyed 竞态防护）
    void graph.render().catch(() => {
      if (!destroyed) {
        console.warn('[G6] graph.render 失败', graph)
      }
    })
    return () => {
      destroyed = true
      graph.destroy()
      graphRef.current = null
    }
  }, [view, typeColors, onSelectNode])

  return <div ref={containerRef} className="h-full w-full" />
}