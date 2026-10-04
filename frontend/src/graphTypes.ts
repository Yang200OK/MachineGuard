import type { CitationSource } from './types'

export type EntityKind = '设备类型' | '组件' | '现象' | '概念' | '检查步骤' | '规则' | '单位' | '文档' | '章节'
export type GraphStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn'
export interface GraphEntity { entity_id: string; name: string; kind: EntityKind; namespace: string; document_id: string; document_title: string }
export interface GraphPathQuery { subject: string; target: string | null; document_id: string | null; max_hops: 1 | 2; limit: number }
export interface GraphPathEdge { relation_id: string; revision: number; subject: GraphEntity; object: GraphEntity; predicate: string; citation: CitationSource['citation']; chunk_sha256: string }
export interface GraphPathResult {
  status: 'paths' | 'no_path' | 'clarification'; reason: string | null; snapshot_id: string; snapshot_manifest: string
  query: GraphPathQuery; paths: { hops: number; edges: GraphPathEdge[] }[]; candidates: (GraphEntity & { field: 'subject' | 'target' })[]
  truncated: boolean; limitation: string
}
export interface GraphSource { chunk_id: string; snapshot_id: string; chunk_sha256: string; title: string; text: string; page: number; section: string; document_id: string; version_id: string; document_version: number; source_metadata: Record<string, string> }
export interface GraphRelation {
  relation_id: string; subject: GraphEntity; object: GraphEntity; predicate: string; snapshot_id: string
  citation: CitationSource['citation']; chunk_sha256: string; note: string; status: GraphStatus; revision: number
  source_available: boolean; usable: boolean; unavailable_reason: string | null; creator: string; created_at: string
}
export interface GraphDetail extends GraphRelation { actions: { action_id: string; kind: 'create' | 'review'; actor: string; revision: number; payload: Record<string, unknown>; result: Record<string, unknown>; created_at: string }[] }
export interface GraphPage<T> { items: T[]; total: number; offset: number; limit: number; snapshot_id?: string | null }
export interface GraphMutation { relation_id: string; status: GraphStatus; revision: number; replayed: boolean; deduplicated?: boolean }
export interface GraphCreateRequest { idempotency_key: string; snapshot_id: string; chunk_id: string; chunk_sha256: string; quote_start: number; quote_end: number; subject: string; subject_kind: EntityKind; object: string; object_kind: EntityKind; predicate: string; note: string }
export interface GraphReviewRequest { idempotency_key: string; expected_revision: number; decision: 'approved' | 'rejected' | 'withdrawn'; reason: string; evidence_checked: boolean; direction_checked: boolean }
