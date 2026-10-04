import type { AuthSession, CurrentUser, DocumentDetail, DocumentVersion, DocumentParse, DocumentSummary, DocumentUpload, Engine, Job, Model, Page, Prediction, PredictionAttempt, SensorResponse, ManagementHistory, ManagementRequest, ManagementResult, ParsedPage, ParsedChunk, ReviewRequest, SourceMetadata } from './types'
import type { KnowledgeSnapshot, SnapshotPage, PublishKnowledgeRequest, SearchPreview } from './types'
import type { KnowledgeAnswer, QuestionRequest } from './types'
import type { ConversationRequest, ConversationDetail, KnowledgeConversation, CitationReference, CitationSource } from './types'
import type { SkillSummary, SkillDetail } from './types'
import type { AgentRun, AgentRequest, AgentEvent } from './types'
import type { ApprovalRequest, ReviewDetail, FinalReport, ReportExport, MaintenanceTask } from './types'
import type { GraphPage, GraphSource, GraphRelation, GraphDetail, GraphCreateRequest, GraphReviewRequest, GraphMutation } from './graphTypes'
import type { GraphPathQuery, GraphPathResult } from './graphTypes'
import type { McpConfig, McpCredential, McpGrant, McpActivity } from './mcpTypes'

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number, public requestId?: string) {
    super(message)
    this.name = 'ApiError'
  }
}

const base = import.meta.env.VITE_API_BASE_URL as string | undefined
if (!base) throw new Error('缺少 VITE_API_BASE_URL，请按 frontend/.env.example 创建本地 .env。')
const url = new URL(base)
if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
  throw new Error('VITE_API_BASE_URL 必须是无凭据、无路径的 HTTP(S) 服务地址。')
}

let unauthorized: (() => void) | undefined
export function onUnauthorized(callback: () => void) { unauthorized = callback }

async function request<T>(path: string, token?: string, method = 'GET', body?: unknown, signal?: AbortSignal, timeoutMs = 20_000): Promise<T> {
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timer = window.setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
  const multipart = body instanceof FormData
  try {
    const response = await fetch(`${url.origin}${path}`, {
      method, signal: controller.signal, cache: 'no-store',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body !== undefined && !multipart ? { 'Content-Type': 'application/json; charset=utf-8' } : {}) },
      ...(body !== undefined ? { body: multipart ? body : JSON.stringify(body) } : {}),
    })
    const payload = await response.json() as { data?: T; request_id?: string; error?: { code: string; message: string } }
    if (!response.ok) {
      if (response.status === 401 && token) unauthorized?.()
      if (!payload.error) throw new ApiError('服务错误响应不符合约定。', 'RESPONSE_INVALID', response.status)
      throw new ApiError(payload.error.message, payload.error.code, response.status, payload.request_id)
    }
    if (!('data' in payload) || typeof payload.request_id !== 'string') {
      throw new ApiError('服务响应不符合约定。', 'RESPONSE_INVALID', response.status)
    }
    return payload.data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (signal?.aborted) throw new DOMException('查询已取消。', 'AbortError')
    if (timedOut) {
      const chat = path.startsWith('/api/chat') || path.startsWith('/api/conversations') || path.startsWith('/api/agent')
      const message = chat ? (method === 'GET' ? '读取会话或引用超时，请刷新核对。' : '请求超时，结果尚未确认。请确认原请求，不要重新发送。') : multipart ? '上传请求超时，结果尚未确认。请查询版本状态，用原请求键和原文件确认。' : '请求超时，结果尚未确认。预测可用同一请求键重试。'
      throw new ApiError(message, 'REQUEST_TIMEOUT', 0)
    }
    if (error instanceof SyntaxError) throw new ApiError('服务未返回有效 JSON。', 'RESPONSE_INVALID', 0)
    throw new ApiError('网络请求失败，请检查 API 服务与连接。', 'NETWORK_ERROR', 0)
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

export async function downloadExport(token: string, reportId: string, exportId: string, format: string, digest: string) {
  const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 20000)
  try {
    const response = await fetch(`${url.origin}/api/reports/${encodeURIComponent(reportId)}/export?export_id=${encodeURIComponent(exportId)}`, { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal, cache: 'no-store' })
    if (!response.ok) {
      if (response.status === 401) unauthorized?.()
      const result = await response.json() as { error: { message: string; code: string } }
      throw new ApiError(result.error.message, result.error.code, response.status)
    }
    const data = await response.arrayBuffer(); const hash = await crypto.subtle.digest('SHA-256', data)
    const actual = Array.from(new Uint8Array(hash)).map(byte => byte.toString(16).padStart(2, '0')).join('')
    if (actual !== digest || response.headers.get('X-Export-SHA256') !== digest) throw new Error('下载文件哈希校验失败。')
    const link = document.createElement('a'); const object = URL.createObjectURL(new Blob([data], { type: response.headers.get('Content-Type') ?? 'application/octet-stream' }))
    link.href = object; link.download = `MachineGuard-${reportId}.${format}`; link.click(); window.setTimeout(() => URL.revokeObjectURL(object), 1000)
  } finally { window.clearTimeout(timer) }
}

export const api = {
  mcpConfig: (token: string, signal?: AbortSignal) => request<McpConfig>('/api/mcp/config', token, 'GET', undefined, signal),
  mcpCredentials: (token: string, offset = 0, signal?: AbortSignal) => request<Page<McpCredential>>(`/api/mcp/credentials?offset=${offset}`, token, 'GET', undefined, signal),
  mcpActivity: (token: string, offset = 0, signal?: AbortSignal) => request<Page<McpActivity>>(`/api/mcp/activity?offset=${offset}`, token, 'GET', undefined, signal),
  issueMcpCredential: (token: string, body: McpGrant) => request<{ credential: McpCredential; access_token: string | null; replayed: boolean }>('/api/mcp/credentials', token, 'POST', body),
  revokeMcpCredential: (token: string, id: string) => request<McpCredential>(`/api/mcp/credentials/${encodeURIComponent(id)}/revoke`, token, 'POST'),
  graphPaths: (token: string, body: GraphPathQuery, signal?: AbortSignal) => request<GraphPathResult>('/api/graph/search', token, 'POST', body, signal),
  analysisPathSource: (token: string, id: string, path: number, edge: number, signal?: AbortSignal) => request<CitationSource>(`/api/agent/runs/${encodeURIComponent(id)}/paths/${path}/${edge}/source`, token, 'GET', undefined, signal),
  graphSources: (token: string, offset = 0, signal?: AbortSignal) => request<GraphPage<GraphSource>>(`/api/graph/sources?offset=${offset}&limit=10`, token, 'GET', undefined, signal),
  graphRelations: (token: string, status: string, available: boolean, offset = 0, signal?: AbortSignal) => request<GraphPage<GraphRelation>>(`/api/graph/relations?status=${encodeURIComponent(status)}&available_only=${available}&offset=${offset}&limit=20`, token, 'GET', undefined, signal),
  graphRelation: (token: string, id: string, signal?: AbortSignal) => request<GraphDetail>(`/api/graph/relations/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  graphSource: (token: string, id: string, signal?: AbortSignal) => request<CitationSource>(`/api/graph/relations/${encodeURIComponent(id)}/source`, token, 'GET', undefined, signal),
  createGraphRelation: (token: string, body: GraphCreateRequest) => request<GraphMutation>('/api/graph/relations', token, 'POST', body),
  reviewGraphRelation: (token: string, id: string, body: GraphReviewRequest) => request<GraphMutation>(`/api/graph/relations/${encodeURIComponent(id)}/review`, token, 'POST', body),
  reportExports: (token: string, id: string, signal?: AbortSignal) => request<{ items: ReportExport[] }>(`/api/reports/${encodeURIComponent(id)}/exports`, token, 'GET', undefined, signal),
  createExport: (token: string, id: string, body: unknown) => request<ReportExport>(`/api/reports/${encodeURIComponent(id)}/exports`, token, 'POST', body),
  maintenance: (token: string, id: string, signal?: AbortSignal) => request<{ items: MaintenanceTask[] }>(`/api/reports/${encodeURIComponent(id)}/maintenance`, token, 'GET', undefined, signal),
  createMaintenance: (token: string, id: string, body: unknown) => request<MaintenanceTask>(`/api/reports/${encodeURIComponent(id)}/maintenance`, token, 'POST', body),
  updateMaintenance: (token: string, id: string, body: unknown) => request<MaintenanceTask>(`/api/maintenance-tasks/${encodeURIComponent(id)}`, token, 'PATCH', body),
  reviews: (token: string, offset = 0, signal?: AbortSignal, view: 'active' | 'history' = 'active') => request<Page<ReviewDetail>>(`/api/reviews?offset=${offset}&limit=10&view=${view}`, token, 'GET', undefined, signal),
  review: (token: string, id: string, signal?: AbortSignal) => request<ReviewDetail>(`/api/reviews/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  reviewers: (token: string, signal?: AbortSignal) => request<{ items: { id: string; display_name: string }[] }>('/api/reviewers', token, 'GET', undefined, signal),
  assignReview: (token: string, id: string, reviewer_id: string | null) => request<ReviewDetail>(`/api/reviews/${encodeURIComponent(id)}/assignment`, token, 'POST', { reviewer_id }),
  approveReport: (token: string, id: string, body: ApprovalRequest) => request<ReviewDetail & { replayed: boolean }>(`/api/agent/runs/${encodeURIComponent(id)}/approval`, token, 'POST', body),
  reviewCitation: (token: string, id: string, claim: number, citation: number, signal?: AbortSignal) => request<CitationSource>(`/api/reviews/${encodeURIComponent(id)}/citations/${claim}/${citation}`, token, 'GET', undefined, signal),
  reports: (token: string, offset = 0, signal?: AbortSignal) => request<Page<FinalReport>>(`/api/reports?offset=${offset}&limit=10`, token, 'GET', undefined, signal),
  report: (token: string, id: string, signal?: AbortSignal) => request<FinalReport>(`/api/reports/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  reportCitation: (token: string, id: string, claim: number, citation: number, signal?: AbortSignal) => request<CitationSource>(`/api/reports/${encodeURIComponent(id)}/citations/${claim}/${citation}`, token, 'GET', undefined, signal),
  submitAnalysis: (token: string, body: AgentRequest) => request<AgentRun & { replayed: boolean }>('/api/agent/runs', token, 'POST', body),
  analyses: (token: string, offset = 0, signal?: AbortSignal, deleted = false) => request<Page<AgentRun>>(`/api/agent/runs?offset=${offset}&limit=10&deleted=${deleted}`, token, 'GET', undefined, signal),
  analysis: (token: string, id: string, signal?: AbortSignal) => request<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  analysisRecord: (token: string, id: string, restore = false) => request<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}/${restore ? 'restore' : 'delete'}`, token, 'POST'),
  analysisEvents: (token: string, id: string, after = 0, signal?: AbortSignal) => request<{ items: AgentEvent[]; last_sequence: number; has_more: boolean }>(`/api/agent/runs/${encodeURIComponent(id)}/events?after=${after}`, token, 'GET', undefined, signal),
  cancelAnalysis: (token: string, id: string) => request<AgentRun>(`/api/agent/runs/${encodeURIComponent(id)}/cancel`, token, 'POST'),
  analysisCitation: (token: string, id: string, claim: number, citation: number, signal?: AbortSignal) => request<CitationSource>(`/api/agent/runs/${encodeURIComponent(id)}/citations/${claim}/${citation}`, token, 'GET', undefined, signal),
  skills: (token: string, signal?: AbortSignal) => request<{ items: SkillSummary[]; registry_sha256: string; max_tool_calls: number; agent_selection_ready: boolean }>('/api/skills', token, 'GET', undefined, signal),
  skill: (token: string, name: string, version: string, signal?: AbortSignal) => request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}?version=${encodeURIComponent(version)}`, token, 'GET', undefined, signal),
  catalog: (token: string, offset: number, search: string, category: string, state: string, archived: boolean, signal?: AbortSignal) => request<Page<DocumentSummary>>(`/api/knowledge/catalog?offset=${offset}&limit=10&search=${encodeURIComponent(search)}&category=${encodeURIComponent(category)}&state=${encodeURIComponent(state)}&archived=${archived}`, token, 'GET', undefined, signal),
  directory: (token: string, id: string, body: { category?: string; archived?: boolean }) => request(`/api/knowledge/documents/${id}/directory`, token, 'PATCH', body),
  documentVersion: (token: string, doc: string, version: string, signal?: AbortSignal) => request<DocumentVersion>(`/api/knowledge/documents/${doc}/versions/${version}`, token, 'GET', undefined, signal),
  conversationRecord: (token: string, id: string, restore = false) => request<KnowledgeConversation>(`/api/conversations/${id}/${restore ? 'restore' : 'delete'}`, token, 'POST'),
  jobRecords: (token: string, ids: string[], restore = false) => request<{ items: { job_id: string; changed: boolean; reason: string | null }[] }>('/api/job-records', token, 'POST', { ids, restore }),
  answerNavigation: (token: string, id: string) => request<{ answer_id: string; conversation_id: string | null }>(`/api/chat/${id}/navigation`, token),
  createConversation: (token: string, body: ConversationRequest) => request<KnowledgeConversation>('/api/conversations', token, 'POST', body),
  conversations: (token: string, offset = 0, signal?: AbortSignal, search = '', deleted = false) => request<Page<KnowledgeConversation>>(`/api/conversations?offset=${offset}&limit=10&search=${encodeURIComponent(search)}&deleted=${deleted}`, token, 'GET', undefined, signal),
  renameConversation: (token: string, id: string, title: string, expectedTitle: string) => request<KnowledgeConversation>(`/api/conversations/${encodeURIComponent(id)}/title`, token, 'PATCH', { title, expected_title: expectedTitle }),
  citationSource: (token: string, ref: CitationReference, signal?: AbortSignal) => request<CitationSource>(`/api/chat/${encodeURIComponent(ref.answer_id)}/citations/${ref.claim_index}/${ref.citation_index}`, token, 'GET', undefined, signal),
  conversation: (token: string, id: string, signal?: AbortSignal) => request<ConversationDetail>(`/api/conversations/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  askKnowledge: (token: string, body: QuestionRequest) => request<KnowledgeAnswer>('/api/chat', token, 'POST', body),
  knowledgeAnswer: (token: string, id: string, signal?: AbortSignal) => request<KnowledgeAnswer>(`/api/chat/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  knowledgeAnswers: (token: string, offset = 0, signal?: AbortSignal) => request<Page<KnowledgeAnswer>>(`/api/chat?offset=${offset}&limit=10`, token, 'GET', undefined, signal),
  publishKnowledge: (token: string, id: string, body: PublishKnowledgeRequest) => request<KnowledgeSnapshot>(`/api/knowledge/versions/${encodeURIComponent(id)}/publish`, token, 'POST', body),
  knowledgeSnapshots: (token: string, offset = 0, signal?: AbortSignal) => request<SnapshotPage>(`/api/knowledge/snapshots?offset=${offset}&limit=10`, token, 'GET', undefined, signal),
  searchPreview: (token: string, query: string, snapshotId: string | null, equipmentType: string | null) => request<SearchPreview>('/api/knowledge/search-preview', token, 'POST', { query, snapshot_id: snapshotId, equipment_type: equipmentType, limit: 5 }, undefined, 75_000),
  originalDocument: async (token: string, versionId: string): Promise<Blob> => {
    const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 60_000)
    try {
      const response = await fetch(`${url.origin}/api/knowledge/versions/${encodeURIComponent(versionId)}/content`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal: controller.signal })
      if (!response.ok) {
        if (response.status === 401) unauthorized?.()
        const payload = await response.json() as { error: { code: string; message: string }; request_id: string }
        throw new ApiError(payload.error.message, payload.error.code, response.status, payload.request_id)
      }
      return await response.blob()
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw new ApiError(controller.signal.aborted ? '读取原文超时，请重新核对连接。' : '读取原文失败，请检查 API 与 OSS 连接。', controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR', 0)
    } finally { window.clearTimeout(timer) }
  },
  documentParses: (token: string, versionId: string, offset = 0, signal?: AbortSignal) => request<Page<DocumentParse>>(`/api/knowledge/versions/${encodeURIComponent(versionId)}/parses?offset=${offset}&limit=20`, token, 'GET', undefined, signal),
  parsePages: (token: string, id: string, offset = 0, signal?: AbortSignal) => request<Page<ParsedPage>>(`/api/knowledge/parses/${encodeURIComponent(id)}/pages?offset=${offset}&limit=4`, token, 'GET', undefined, signal),
  parseChunks: (token: string, id: string, offset = 0, signal?: AbortSignal) => request<Page<ParsedChunk>>(`/api/knowledge/parses/${encodeURIComponent(id)}/chunks?offset=${offset}&limit=5`, token, 'GET', undefined, signal),
  management: (token: string, id: string, offset = 0, signal?: AbortSignal) => request<ManagementHistory>(`/api/knowledge/versions/${encodeURIComponent(id)}/management?offset=${offset}&limit=10`, token, 'GET', undefined, signal),
  correctMetadata: (token: string, id: string, body: ManagementRequest & { metadata: SourceMetadata }) => request<ManagementResult>(`/api/knowledge/versions/${encodeURIComponent(id)}/metadata`, token, 'PATCH', body),
  deactivateVersion: (token: string, id: string, body: ManagementRequest) => request<ManagementResult>(`/api/knowledge/versions/${encodeURIComponent(id)}/deactivate`, token, 'POST', body),
  reviewParse: (token: string, id: string, body: ReviewRequest) => request<ManagementResult>(`/api/knowledge/parses/${encodeURIComponent(id)}/review`, token, 'POST', body),
  parseDocument: (token: string, versionId: string, mode: 'native' | 'vision', key: string) => request<DocumentParse>(`/api/knowledge/versions/${encodeURIComponent(versionId)}/parse`, token, 'POST', { mode, idempotency_key: key }),
  documents: (token: string, offset = 0, limit = 10, signal?: AbortSignal) => request<Page<DocumentSummary>>(`/api/knowledge/documents?offset=${offset}&limit=${limit}`, token, 'GET', undefined, signal),
  document: (token: string, id: string, offset = 0, limit = 10, signal?: AbortSignal) => request<DocumentDetail>(`/api/knowledge/documents/${encodeURIComponent(id)}?offset=${offset}&limit=${limit}`, token, 'GET', undefined, signal),
  uploadDocument: (token: string, body: FormData, documentId: string | null) => request<DocumentUpload>(documentId ? `/api/knowledge/documents/${encodeURIComponent(documentId)}/versions` : '/api/knowledge/documents', token, 'POST', body, undefined, 60_000),
  submitModelCheck: (token: string, modelVersion: string, key: string) => request<Job>('/api/jobs', token, 'POST', { kind: 'model_integrity_check', model_version: modelVersion, idempotency_key: key }),
  jobs: (token: string, offset = 0, limit = 10, signal?: AbortSignal, deleted = false) => request<Page<Job>>(`/api/jobs?offset=${offset}&limit=${limit}&deleted=${deleted}`, token, 'GET', undefined, signal),
  job: (token: string, id: string, signal?: AbortSignal) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`, token, 'GET', undefined, signal),
  cancelJob: (token: string, id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`, token, 'POST'),
  login: (username: string, password: string) => request<AuthSession>('/api/auth/login', undefined, 'POST', { username, password }),
  guest: () => request<AuthSession>('/api/auth/guest', undefined, 'POST'),
  me: (token: string) => request<CurrentUser>('/api/auth/me', token),
  logout: (token: string) => request<{ revoked: boolean }>('/api/auth/logout', token, 'POST'),
  models: (token: string, signal?: AbortSignal) => request<{ items: Model[] }>('/api/models', token, 'GET', undefined, signal),
  engines: (token: string, offset = 0, limit = 20, signal?: AbortSignal) => request<Page<Engine>>(`/api/engines?offset=${offset}&limit=${limit}`, token, 'GET', undefined, signal),
  sensors: async (token: string, key: string, start: number, end: number, columns: string[], signal?: AbortSignal) => {
    const query = new URLSearchParams({ start_cycle: String(start), end_cycle: String(end) })
    columns.forEach(column => query.append('columns', column))
    const data = await request<SensorResponse>(`/api/engines/${encodeURIComponent(key)}/sensors?${query}`, token, 'GET', undefined, signal)
    if (data.engine.engine_key !== key || data.start_cycle !== start || data.end_cycle !== end ||
        data.records.length !== end - start + 1 || data.records.some((row, index) => row.cycle !== start + index ||
        columns.some(column => typeof row[column] !== 'number' || !Number.isFinite(row[column])))) {
      throw new ApiError('观测响应的设备、周期或数值不符合请求边界。', 'RESPONSE_INVALID', 200)
    }
    return data
  },
  predict: (token: string, attempt: PredictionAttempt) => request<Prediction>('/api/predictions', token, 'POST', attempt),
  history: (token: string, key: string, end: number, offset = 0, limit = 10, signal?: AbortSignal) => request<Page<Prediction>>(`/api/engines/${encodeURIComponent(key)}/predictions?end_cycle=${end}&offset=${offset}&limit=${limit}`, token, 'GET', undefined, signal),
}
