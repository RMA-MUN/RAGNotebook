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
const DOC_FILL = '#8b5cf6'
const FALLBACK_FILL = '#64748b'
const RECENTRE_DELAY_MS = 900
const RECENTRE_ANIMATION = { duration: 450 }
const RESIZE_DEBOUNCE_MS = 200

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
const ROOT_BIRTH_JITTER = 10 // 首帧根节点（笔记）在原点处的抖动半径：全部堆在视口中心起爆
const ALPHA_INCREMENTAL = 0.25 // SSE 增量到货时的轻唤醒
const ALPHA_DRAG_TARGET = 0.45 // 拖拽期间的能量水平：决定力传导到邻域的"活性"；约束已由弹簧+重力提供，能量可以放开
const ALPHA_RELEASE_CAP = 0.3 // 松手保留适量余能：弹簧回收过程可见（弹性回弹），随后自然冷却
const ALPHA_MIN_STOP = 0.02

function nodeRadius(n: Pick<GraphNode, 'node_type'>): number {
  if (n.node_type === 'note') return 10
  if (n.node_type === 'doc') return 12
  return 14
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

/** 合帧绘制：setData 后与物理 tick 共用，避免重入排队；gate 存放进行中的绘制 Promise，可据此衔接首帧后的视口操作 */
function scheduleDraw(graph: Graph, gate: { current: Promise<void> | null }): Promise<void> {
  if (gate.current) return gate.current
  // draw() 的异步管线不可取消，入口守卫拦不住「调用后才销毁」：开发环境 StrictMode 双挂载
  // 会在首轮 draw 管线执行中途销毁实例，G6 内部打出 destroyed 告警。推迟到下一帧发起并
  // 在发起前复查销毁状态——StrictMode 的销毁总在同步 commit 周期内完成，必然早于下一帧。
  if (graph.destroyed) return Promise.resolve()
  const deferred = new Promise<void>((resolve) => {
    requestAnimationFrame(() => {
      if (graph.destroyed) {
        resolve()
        return
      }
      graph.draw()
        .catch((err: unknown) => {
          if (!graph.destroyed) console.warn('[graph] 帧绘制失败:', err)
        })
        .then(() => resolve())
    })
  })
  gate.current = deferred
  deferred.then(() => {
    if (gate.current === deferred) gate.current = null
  })
  return deferred
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
  const drawingRef = useRef<Promise<void> | null>(null)
  // G6 的画布/相机在首次 draw 前不存在，视口类 API 必须等首帧绘制完成才能调用
  const canvasReadyRef = useRef(false)

  // 图实例与模拟器只创建/销毁一次；G6 只做样式映射与逐帧回写渲染，物理由外部 d3-force 驱动
  useEffect(() => {
    if (!containerRef.current) return
    const container = containerRef.current
    const graph = new Graph({
      container,
      // 显式传入容器尺寸：G6 自测容器在某些时序下会把高度量成 0，导致画布配置高度错误、
      // 视口居中算出 cy=0（整场爆炸贴顶）。挂载后布局已稳定，此处量得的就是最终尺寸；
      // 后续尺寸变化由下方 ResizeObserver 走 graph.resize 跟随
      width: container.clientWidth,
      height: container.clientHeight,
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

    // 容器尺寸跟随：未开 autoResize 时画布会停留在旧尺寸（显示不全）。防抖合并连续触发；
    // 已适配过视野则重新 fitView，入场进行中则仅保持世界原点居中
    let resizeTimer: ReturnType<typeof setTimeout> | null = null
    const resizeObserver = new ResizeObserver(() => {
      if (resizeTimer) clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        resizeTimer = null
        const g = graphRef.current
        const el = containerRef.current
        if (!g || g.destroyed || !el) return
        g.resize(el.clientWidth, el.clientHeight)
        if (initialFitDone) void g.fitView(undefined, RECENTRE_ANIMATION)
        else if (canvasReadyRef.current) void g.fitCenter(RECENTRE_ANIMATION)
      }, RESIZE_DEBOUNCE_MS)
    })
    resizeObserver.observe(containerRef.current)

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
      resizeObserver.disconnect()
      if (resizeTimer) clearTimeout(resizeTimer)
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
      drawingRef.current = null
      canvasReadyRef.current = false
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
    const placed = new Map<string, SimNode>()
    // 第一遍：沿用旧位置；首次渲染的根节点（笔记）直接在世界原点出生——
    // 原点即视口中心，整张图从屏幕正中堆叠起爆，而不是散落在中心四周
    for (const n of view.nodes) {
      const old = prev.get(n.id)
      if (old) {
        nextNodes.push(old)
        placed.set(n.id, old)
      } else if (isFirstRender && n.node_type === 'note') {
        structuralChange = true
        const node: SimNode = { id: n.id, r: nodeRadius(n), ...randOffset(ROOT_BIRTH_JITTER) }
        nextNodes.push(node)
        placed.set(n.id, node)
      }
    }
    // 第二遍：其余新节点从已就位的邻居"长出来"（可锚定本轮先放置的节点），无邻居可用才在中心附近散布
    for (const n of view.nodes) {
      if (placed.has(n.id)) continue
      structuralChange = true
      const node: SimNode = { id: n.id, r: nodeRadius(n), ...birthPosition(n.id, view.edges, placed) }
      nextNodes.push(node)
      placed.set(n.id, node)
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
          type: n.node_type === 'note' ? 'rect' : n.node_type === 'doc' ? 'diamond' : 'circle',
          data: { label: n.label, node_type: n.node_type, entity_type_id: n.entity_type_id },
          style: {
            size: n.node_type === 'note' ? 16 : n.node_type === 'doc' ? 20 : 24,
            fill: n.node_type === 'note'
              ? NOTE_FILL
              : n.node_type === 'doc'
                ? DOC_FILL
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
    const drawn = scheduleDraw(graph, drawingRef)

    // 首帧绘制完成后把真实内容包围盒中心对齐到视口中心：d3 重力把布局往世界原点 (0,0) 收拢，
    // 但首帧节点还没收拢到原点（从邻居 birthPosition/randOffset 散落出生），质心 ≠ (0,0)，
    // 若像旧逻辑用 translateTo(canvasCenter) 只钉原点，首帧就会偏出屏幕正中。
    // 用 fitCenter（只居中不缩放，见 G6 viewport.js focus(): delta = canvasCenter - bboxCenter）——
    // 既满足「炸开前就在正中」，又避免过早 fitView 缩放导致爆炸过程跑偏
    if (isFirstRender) {
      void drawn.then(() => {
        const el = containerRef.current
        if (graph.destroyed || !el) return
        canvasReadyRef.current = true
        // 先按最新容器尺寸校正画布（防御初始化与首帧之间的布局变动），再按真实内容居中
        graph.resize(el.clientWidth, el.clientHeight)
        graph.fitCenter(RECENTRE_ANIMATION).catch((err: unknown) => {
          console.warn('[graph] 视口居中失败:', err)
        })
      })
    }

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
