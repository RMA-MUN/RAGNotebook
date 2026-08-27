import { useEffect, useRef } from 'react'
import { Graph } from '@antv/g6'
import type { IElementEvent } from '@antv/g6'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force'
import type { Simulation, SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import { useThemeStore } from '../../stores/useThemeStore'
import type { GraphEdge, GraphNode, GraphView } from '../../types/graph'

interface Props {
  view: GraphView
  typeColors: Record<string, string>
  onSelectNode?: (nodeId: string) => void
  /** 手动刷新时递增：数据收敛后平滑回中视野；自动刷新不递增 */
  fitSignal?: number
}

interface SimNode extends SimulationNodeDatum {
  id: string
  r: number
}

type SimLink = SimulationLinkDatum<SimNode>

/** 浅/深主题下的画布描边与文字颜色；节点主色仍由类型色板决定 */
const PALETTES = {
  light: { nodeStroke: '#ffffff', edgeStroke: '#94a3b8', label: '#334155' },
  dark: { nodeStroke: '#334155', edgeStroke: '#475569', label: '#cbd5e1' },
} as const

const NOTE_FILL = '#94a3b8'
const FALLBACK_FILL = '#64748b'
const RECENTRE_DELAY_MS = 900
const RECENTRE_ANIMATION = { duration: 450 }

// 力学参数：斥力使孤立节点散开，弹簧沿边聚拢关联节点，重力把整体收在原点附近。
// 星型结构（单笔记挂大量实体）全靠斥力撑开空间；弹簧必须显式加强——
// d3 默认按度数分摊强度，会让星型枢纽上的边软到几乎无力回拉，表现为拖拽后枝杈越飘越远
const CHARGE_STRENGTH = -380
const LINK_DISTANCE = 115
const LINK_STRENGTH = 0.32 // 覆盖 d3 度数分摊的默认值：枢纽边保持有效回拉力
const GRAVITY_STRENGTH = 0.04 // 各轴向原点的距离比例回拉，防止任何成分永久漂移
const COLLIDE_PADDING = 8
const DRAG_START_PX = 4 // 位移超过该值才算拖拽，否则视为点击选中
const ALPHA_INIT = 1 // 首帧起爆（大爆炸→冷却收敛）
const ALPHA_INCREMENTAL = 0.25 // SSE 增量到货时的轻唤醒
const ALPHA_DRAG_TARGET = 0.45 // 拖拽期间的能量水平：决定力传导到邻域的"活性"；约束已由弹簧+重力提供，能量可以放开
const ALPHA_RELEASE_CAP = 0.3 // 松手保留适量余能：弹簧回收过程可见（弹性回弹），随后自然冷却
const ALPHA_MIN_STOP = 0.02

function nodeRadius(n: Pick<GraphNode, 'node_type'>): number {
  return n.node_type === 'note' ? 10 : 14
}

function randOffset(radius: number): { x: number; y: number } {
  const angle = Math.random() * Math.PI * 2
  const r = radius * (0.35 + Math.random() * 0.65)
  return { x: Math.cos(angle) * r, y: Math.sin(angle) * r }
}

/** 新节点的出生位置：优先贴着已存在的邻居（SSE 到货时从关联处"长出来"），否则中心附近散布 */
function birthPosition(id: string, edges: GraphEdge[], existing: Map<string, SimNode>) {
  for (const e of edges) {
    const partner = e.source === id ? e.target : e.target === id ? e.source : null
    if (!partner) continue
    const anchor = existing.get(partner)
    if (anchor?.x !== undefined && anchor.y !== undefined) {
      const off = randOffset(90)
      return { x: anchor.x + off.x, y: anchor.y + off.y }
    }
  }
  return randOffset(150)
}

/** 合帧绘制：setData 后与物理 tick 共用，避免重入排队 */
function scheduleDraw(graph: Graph, gate: { current: boolean }) {
  if (gate.current) return
  gate.current = true
  void graph.draw()
    .catch((err: unknown) => {
      if (!graph.destroyed) console.warn('[graph] 帧绘制失败:', err)
    })
    .finally(() => {
      gate.current = false
    })
}

export function GraphCanvas({ view, typeColors, onSelectNode, fitSignal = 0 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const graphRef = useRef<Graph | null>(null)
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const onSelectNodeRef = useRef(onSelectNode)
  useEffect(() => {
    onSelectNodeRef.current = onSelectNode
  })
  const theme = useThemeStore((s) => s.theme)
  const palette = PALETTES[theme]

  const renderedOnceRef = useRef(false)
  const lastFitSignalRef = useRef(0)
  const drawingRef = useRef(false)

  // 图实例与模拟器只创建/销毁一次；G6 只做样式映射与逐帧回写渲染，物理由外部 d3-force 驱动
  useEffect(() => {
    if (!containerRef.current) return
    const graph = new Graph({
      container: containerRef.current,
      // 关闭全局更新动画：物理 tick 每帧回写坐标，若经补间会永远追赶滞后，表现为"拖拽不跟手"
      animation: false,
      node: { style: { lineWidth: 1, stroke: palette.nodeStroke } },
      edge: { style: { endArrow: true } },
      behaviors: ['drag-canvas', 'zoom-canvas', 'click-select', 'auto-adapt-label'],
    })
    graphRef.current = graph

    const sim = forceSimulation<SimNode>([])
      .force('charge', forceManyBody<SimNode>().strength(CHARGE_STRENGTH))
      // 显式弹簧强度 + 双轴重力：两者共同保证任何被拖散的成分都会缓慢但确定地归位，
      // 仅靠 forceCenter（只平移质心）无法阻止结构与枢纽间越拉越远
      .force('x', forceX<SimNode>(0).strength(GRAVITY_STRENGTH))
      .force('y', forceY<SimNode>(0).strength(GRAVITY_STRENGTH))
      .force('collide', forceCollide<SimNode>().radius((n) => n.r + COLLIDE_PADDING).iterations(1))
    sim.stop() // 空数据不起转，由数据对账显式启动
    sim.alphaMin(ALPHA_MIN_STOP)
    simRef.current = sim

    // 视野适配绑定在收敛完成时：过早适配会在扩散过程中跑偏（见截图教训）
    let initialFitDone = false
    sim.on('end', () => {
      if (initialFitDone || !renderedOnceRef.current) return
      initialFitDone = true
      const g = graphRef.current
      if (!g || g.destroyed) return
      void g.fitView(undefined, RECENTRE_ANIMATION)
    })

    // 拖拽采用"增量映射"，不经过任何绝对坐标换算：以节点当前世界坐标为锚，
    // 把指针的屏幕位移除以相机缩放后累加到钉扎点。DPR、容器偏移、页面缩放等
    // 一切参考系因素在增量中全部抵消，抓取点也不会跳变（d3-drag 同款思路）。
    // 疑难排查：控制台执行 localStorage.setItem('graph_drag_debug','1') 后刷新可开启链路日志。
    const findSimNode = (evt: { target?: unknown }): SimNode | null => {
      const id = ((evt.target as { id?: string }) ?? {}).id
      return id ? nodesRef.current.find((n) => n.id === id) ?? null : null
    }
    const debugLog = (...args: unknown[]) => {
      if (localStorage.getItem('graph_drag_debug') === '1') console.debug('[graph-drag]', ...args)
    }

    let activeDrag: { node: SimNode; lastX: number | null; lastY: number | null; moved: boolean } | null = null
    let swallowNextClick = false

    const nativeClientOf = (src: unknown): { x: number; y: number } | null => {
      const e = src as {
        nativeEvent?: { clientX?: unknown; clientY?: unknown }
        clientX?: unknown
        clientY?: unknown
      }
      const cx = e.nativeEvent?.clientX ?? e.clientX
      const cy = e.nativeEvent?.clientY ?? e.clientY
      return typeof cx === 'number' && typeof cy === 'number'
        ? { x: cx, y: cy }
        : null
    }

    const onWindowPointerMove = (ev: PointerEvent) => {
      if (!activeDrag) return
      const c = nativeClientOf(ev)
      if (!c) return
      if (!activeDrag.moved &&
          activeDrag.lastX !== null && activeDrag.lastY !== null &&
          Math.hypot(c.x - activeDrag.lastX, c.y - activeDrag.lastY) > DRAG_START_PX) {
        activeDrag.moved = true
        debugLog('drag-start', activeDrag.node.id)
      }
      if (activeDrag.moved) {
        const n = activeDrag.node
        const k = graph.getZoom() || 1
        if (activeDrag.lastX !== null && activeDrag.lastY !== null) {
          // 锚定节点自身位置 + 屏幕位移/缩放比例；取整误差可忽略
          n.fx = (n.x ?? 0) + (c.x - activeDrag.lastX) / k
          n.fy = (n.y ?? 0) + (c.y - activeDrag.lastY) / k
        } else {
          n.fx = n.x ?? 0
          n.fy = n.y ?? 0
        }
      }
      activeDrag.lastX = c.x
      activeDrag.lastY = c.y
    }
    const endDrag = () => {
      window.removeEventListener('pointermove', onWindowPointerMove)
      window.removeEventListener('pointerup', endDrag)
      window.removeEventListener('pointercancel', endDrag)
      if (!activeDrag) return
      swallowNextClick = activeDrag.moved
      debugLog('drag-end', activeDrag.node.id, 'moved=', activeDrag.moved)
      activeDrag.node.fx = undefined
      activeDrag.node.fy = undefined
      activeDrag = null
      // 松手即降温：压低当前能量让弹簧与重力尽快回收结构，避免带着余热继续外漂
      sim.alphaTarget(0)
      sim.alpha(Math.min(sim.alpha(), ALPHA_RELEASE_CAP))
      sim.restart()
    }
    graph.on('node:pointerdown', (evt: unknown) => {
      const node = findSimNode((evt ?? {}) as { target?: unknown })
      if (!node) {
        debugLog('pointerdown: 无匹配节点', (evt as { target?: { id?: string } })?.target)
        return
      }
      const c = nativeClientOf(evt)
      activeDrag = {
        node,
        // 不动节点位置，只在真正产生位移后才开始映射——按下瞬间零跳变
        lastX: c ? c.x : null,
        lastY: c ? c.y : null,
        moved: false,
      }
      sim.alphaTarget(ALPHA_DRAG_TARGET)
      sim.restart()
      debugLog('pointerdown', node.id, 'native=', c, 'pos=', [node.x ?? 0, node.y ?? 0])
      window.addEventListener('pointermove', onWindowPointerMove)
      window.addEventListener('pointerup', endDrag)
      window.addEventListener('pointercancel', endDrag)
    })

    graph.on('node:click', (evt: IElementEvent) => {
      if (swallowNextClick) {
        swallowNextClick = false
        return
      }
      const id = (evt.target as { id?: string })?.id
      if (id) onSelectNodeRef.current?.(String(id))
    })

    // 物理 tick 只回写坐标并重绘，绕开 G6 的 layout 流水线——这是视角不被打扰的关键
    sim.on('tick', () => {
      const g = graphRef.current
      if (!g || g.destroyed) return
      const ns = nodesRef.current
      if (!ns.length) return
      g.updateNodeData(ns.map((n) => ({ id: n.id, style: { x: n.x ?? 0, y: n.y ?? 0 } })))
      scheduleDraw(g, drawingRef)
    })

    return () => {
      window.removeEventListener('pointermove', onWindowPointerMove)
      window.removeEventListener('pointerup', endDrag)
      window.removeEventListener('pointercancel', endDrag)
      sim.stop()
      sim.on('tick', null)
      sim.on('end', null)
      graph.destroy()
      graphRef.current = null
      simRef.current = null
      nodesRef.current = []
      drawingRef.current = false
      renderedOnceRef.current = false
    }
    // 初始配色仅取决于挂载时主题；后续主题切换走数据对账路径重映射样式
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 数据/样式对账：全量 setData 应用样式映射与坐标快照，随后按结构变化幅度决定启动/唤醒模拟器
  useEffect(() => {
    const graph = graphRef.current
    const sim = simRef.current
    if (!graph || !sim || graph.destroyed) return

    const isFirstRender = !renderedOnceRef.current
    const prev = new Map(nodesRef.current.map((n) => [n.id, n]))
    let structuralChange = isFirstRender
    const nextNodes: SimNode[] = []
    for (const n of view.nodes) {
      const old = prev.get(n.id)
      if (old) {
        nextNodes.push(old)
      } else {
        structuralChange = true
        nextNodes.push({
          id: n.id,
          r: nodeRadius(n),
          ...(isFirstRender ? randOffset(150) : birthPosition(n.id, view.edges, prev)),
        })
      }
    }
    if (nextNodes.length !== prev.size) structuralChange = true
    nodesRef.current = nextNodes

    const nodeIds = new Set(view.nodes.map((n) => n.id))
    const links: SimLink[] = view.edges
      // 防御：丢弃端点不在节点集中的边（后端偶发悬空边不应炸掉整页）
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }))
    graph.setData({
      nodes: view.nodes.map((n) => {
        const pos = nextNodes.find((p) => p.id === n.id)
        return {
          id: n.id,
          type: n.node_type === 'note' ? 'rect' : 'circle',
          data: { label: n.label, node_type: n.node_type, entity_type_id: n.entity_type_id },
          style: {
            size: n.node_type === 'note' ? 16 : 24,
            fill: n.node_type === 'note'
              ? NOTE_FILL
              : (n.entity_type_id && typeColors[n.entity_type_id]) || FALLBACK_FILL,
            stroke: palette.nodeStroke,
            labelText: n.label,
            labelPlacement: 'bottom',
            labelFill: palette.label,
            x: pos?.x ?? 0,
            y: pos?.y ?? 0,
          },
        }
      }),
      edges: view.edges
        .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          style: {
            stroke: palette.edgeStroke,
            lineWidth: 1,
            labelText: e.relation_type || undefined,
            labelFontSize: 10,
            labelFill: palette.label,
            endArrow: true,
          },
        })),
    })
    renderedOnceRef.current = true

    // 无结构变化（如切主题/类型筛选）时模拟器可能静止，确保样式映射立即被绘制出来
    scheduleDraw(graph, drawingRef)

    sim.nodes(nextNodes)
    sim.force(
      'link',
      forceLink<SimNode, SimLink>(links)
        .id((d) => d.id)
        .distance(LINK_DISTANCE)
        .strength(LINK_STRENGTH),
    )

    if (structuralChange) {
      sim.alpha(isFirstRender ? ALPHA_INIT : ALPHA_INCREMENTAL)
      sim.restart()
    }
  }, [view, typeColors, palette])

  // 手动刷新：等增量到货并短暂成形后平滑回中
  useEffect(() => {
    if (!fitSignal || fitSignal === lastFitSignalRef.current) return
    lastFitSignalRef.current = fitSignal
    const timer = setTimeout(() => {
      const g = graphRef.current
      if (!g || g.destroyed || !renderedOnceRef.current) return
      void g.fitView(undefined, RECENTRE_ANIMATION)
    }, RECENTRE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [fitSignal])

  return <div ref={containerRef} className="h-full w-full" />
}
