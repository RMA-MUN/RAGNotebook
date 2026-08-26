export interface GraphNode {
  id: string
  label: string
  node_type: 'entity' | 'note'
  entity_type_id?: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: 'relation' | 'wiki'
  relation_type?: string | null
}

export interface GraphView {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface GraphEntity {
  id: string
  name: string
  display_name: string
  type_id?: string | null
  description?: string | null
  aliases: string[]
  confidence: number
  source_note_ids: string[]
}

export interface EntityNoteLink {
  entity_id: string
  note_id: string
  mention_count: number
  context: { snippet: string }[]
}

export interface EntityType {
  id: string
  user_id?: string | null
  name: string
  display_name: string
  color: string
  icon?: string | null
  is_system: boolean
}

export interface ExtractLog {
  note_id: string
  content_hash: string
  status: 'pending' | 'success' | 'failed'
  new_count: number
  update_count: number
  error_message?: string | null
}

export interface GraphSearchResult {
  entities: { id: string; name: string; type_id?: string | null }[]
  notes: { id: string; title: string }[]
}

export interface GraphSSEEvent {
  type: 'extract_done' | 'extract_failed' | 'ping'
  note_id?: string
  status?: string
  new_count?: number
  update_count?: number
  error?: string
}