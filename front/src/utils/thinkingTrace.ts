export interface ThinkingEvidence {
  id: string
  source: string
  title: string
  score?: number | null
  url?: string | null
  preview: string
}

export interface ThinkingStep {
  stage: string
  content: string
  details?: Record<string, unknown>
  evidence?: ThinkingEvidence[]
}

const retrievalStages = new Set(['local_retrieval', 'web_search', 'supplemental_retrieval'])
const readableStatuses = new Set(['searching', 'empty', 'error', 'dedup', 'limit'])
const visibleThinkingStages = new Set(['agentic_plan', ...retrievalStages])

export const EVIDENCE_PREVIEW_LENGTH = 200

export function isRetrievalStage(stage: string): boolean {
  return retrievalStages.has(stage)
}

export function isVisibleThinkingStage(stage: string): boolean {
  return visibleThinkingStages.has(stage)
}

export function isEvidenceEvent(stage: string, details?: Record<string, unknown>): boolean {
  return isRetrievalStage(stage) && details?.status === 'evidence'
}

export function isReadableRetrievalStatus(stage: string, details?: Record<string, unknown>): boolean {
  return isRetrievalStage(stage) && typeof details?.status === 'string' && readableStatuses.has(details.status)
}

export function retrievalStatusLabel(status: unknown, stage?: string): string {
  switch (status) {
    case 'searching':
      if (stage === 'local_retrieval') return '正在检索本地资料'
      if (stage === 'web_search') return '正在搜索外部资料'
      if (stage === 'supplemental_retrieval') return '正在进行补充检索'
      return '正在检索资料'
    case 'empty': return '未找到相关证据'
    case 'error': return '检索失败'
    case 'dedup': return '已去除重复证据'
    case 'limit': return '已达到证据数量上限'
    default: return ''
  }
}

export function previewEvidence(content: string): string {
  return content.slice(0, EVIDENCE_PREVIEW_LENGTH)
}

export function toEvidence(value: unknown): ThinkingEvidence | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null

  const item = value as Record<string, unknown>
  if (typeof item.id !== 'string' || !item.id) return null

  return {
    id: item.id,
    source: typeof item.source === 'string' ? item.source : '',
    title: typeof item.title === 'string' && item.title ? item.title : '未命名证据',
    score: typeof item.score === 'number' ? item.score : undefined,
    preview: typeof item.preview === 'string' ? item.preview : '',
    url: typeof item.url === 'string' ? item.url : undefined,
  }
}

export function mergeThinkingStep(previous: ThinkingStep, next: ThinkingStep): ThinkingStep {
  if (!isRetrievalStage(previous.stage) || previous.stage !== next.stage) return next

  return {
    ...next,
    evidence: [...(previous.evidence ?? []), ...(next.evidence ?? [])],
  }
}
