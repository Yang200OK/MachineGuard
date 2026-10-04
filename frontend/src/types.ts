/** 对齐后端业务契约，不用真实标签或前端计算代替模型结果。 */
import type { GraphPathResult } from './graphTypes'
export interface SkillSummary {
  name: string; description: string; version: string; sha256: string
  candidate_available?: boolean; status: 'available' | 'unavailable'; reason: string | null; tools: string[]; versions: string[]
}
export interface ToolSchema {
  type: 'function'; function: { name: string; description: string; parameters: Record<string, unknown> }; writes_prediction: boolean
}
export interface SkillDetail extends SkillSummary { body: string; is_active_version: boolean; tool_schemas: ToolSchema[] }
export interface OneClickOptions { start_cycle: number; columns: string[]; include_knowledge: boolean }
export interface AgentRequest { question: string; engine_key: string | null; end_cycle: number | null; idempotency_key: string; workflow?: 'agent' | 'one_click'; workflow_options?: OneClickOptions | null }
export interface ReportCandidate {
  scope: AgentRun['scope']; schema: string; candidate_id: string; status: 'unreviewed'; title: string; workflow: 'fixed_one_click';
  prediction: Prediction; trend: SensorResponse; knowledge_status: 'answered' | 'refused' | 'not_requested';
  sections: { id: string; kind: 'prediction' | 'trend' | 'knowledge'; text: string; unit?: string; claim_index?: number; citations?: AnswerCitation[] }[];
  review_focus: { code: string; text: string }[]; gaps: string[]; limitations: string[];
  skill: { name: string; version: string; sha256: string }
}
export interface AgentEvent { sequence: number; kind: string; details: Record<string, unknown>; created_at: string }
export interface ReportDecision { id: string; decision: 'approved' | 'rejected'; comment: string; reviewer_id: string; reviewer_name: string; created_at: string; candidate_sha256: string; checkpoint_id: string }
export interface ReportReview { status: 'pending' | 'approved' | 'rejected'; started_at: string; approval_available: boolean; assigned_reviewer_id: string | null; candidate_sha256: string; decision: ReportDecision | null; report_id: string | null }
export interface ApprovalRequest { decision: 'approved' | 'rejected'; comment: string; idempotency_key: string; candidate_sha256: string; checkpoint_id: string }
export interface ReviewDetail { run_id: string; owner_name: string; question: string; candidate: ReportCandidate; review: ReportReview; job: Job; model_calls: number; tool_calls: number; checkpoint_id: string }
export interface FinalReport { provenance?: { candidate: ReportCandidate; question: string; owner_name: string; submitted_at: string; model_calls: number; tool_calls: number }; report_id: string; run_id: string; created_at: string; content_sha256: string; content: Omit<ReportCandidate, 'status'> & { status: 'approved'; review: ReportDecision } }
export interface AgentRun {
  workflow: 'agent' | 'one_click'; workflow_options: OneClickOptions | null; run_id: string; question: string; scope: { engine_key: string | null; end_cycle: number | null; data_version: string | null; model_version: string | null; snapshot_id: string | null }
  selected_skill: SkillSummary | null; model_calls: number; tool_calls: number; event_sequence: number; job: Job
  recovery: { version: string; thread_id: string; checkpoint_id: string | null } | null
  review: ReportReview | null
  result: { evidence_paths?: GraphPathResult; report_candidate?: ReportCandidate; status: 'completed' | 'refused' | 'clarification'; reason: string | null; limitation?: string;
    observations: { tool: string; result: Record<string, unknown>; validation: string }[];
    knowledge: { status: 'answered' | 'refused'; claims: { text: string; citations: AnswerCitation[] }[]; reason: string | null } | null } | null
}
export interface User {
  id: string
  username: string
  display_name: string
  roles: string[]
  permissions: string[]
  is_guest: boolean
  is_active: boolean
  created_at: string
}
export interface AuthSession {
  access_token: string
  token_type: string
  expires_at: string
  user: User
}
export interface CurrentUser { user: User; session_id: string; expires_at: string }
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface Engine {
  engine_key: string
  dataset_id: string
  dataset: string
  split: string
  data_version: string
  engine_id: number
  last_observed_cycle: number
  is_demo: boolean
  source: string
  rul_unit: string
}
export interface Metrics { samples: number; mae: number; mse: number; rmse: number }
export interface Model {
  model_version: string
  name: string
  status: string
  manifest_sha256: string
  weight_sha256: string
  configuration: {
    training: Record<string, string | number | boolean>
    completed_epochs: number
    best_epoch: number
    window_size: number
    features: string[]
    device: string
  }
  metrics: { validation: Metrics; official_test_terminal: Metrics; evaluation_sha256: string }
}
export interface RiskRule { version: string; critical_max: number; warning_max: number; unit: string; limitation: string }
export interface Prediction {
  prediction_id: string
  engine_key: string
  model_version: string
  window_start_cycle: number
  end_cycle: number
  window_size: number
  input_sha256: string
  raw_rul: number
  business_rul: number
  postprocessing: string
  duration_ms: number
  device: string
  rul_unit: string
  risk_level: 'normal' | 'warning' | 'critical'
  risk_rule: RiskRule
  request_id: string
  created_at: string
  replayed?: boolean
}
export interface PredictionAttempt {
  engine_key: string
  end_cycle: number
  model_version: string
  idempotency_key: string
}
export interface Trend {
  field: string
  unit: string
  mean: number
  first: number
  last: number
  change: number
  relative_change: number | null
  relative_change_status: string
  slope_per_cycle: number | null
  observation: string
}
export interface SensorResponse {
  engine: Engine
  start_cycle: number
  end_cycle: number
  records: ({ cycle: number } & Record<string, number>)[]
  trends: Trend[]
  method: string
  limitation: string
}

/** 后台任务状态以服务器持久化记录为准。 */
export type JobStatus = 'queued' | 'running' | 'waiting_review' | 'succeeded' | 'failed' | 'cancelled'
export interface JobAttempt {
  number: number
  worker_id: string
  status: JobStatus | 'lease_expired' | 'paused'
  stage: string
  started_at: string
  heartbeat_at: string
  finished_at: string | null
  error_code: string | null
  error_message: string | null
}
export interface Job {
  deleted_at?: string | null
  answer_available?: boolean
  job_id: string
  kind: 'model_integrity_check' | 'document_parse' | 'document_visual_parse' | 'knowledge_index' | 'knowledge_answer' | 'agent_run' | 'report_export'
  status: JobStatus
  stage: string
  progress: number
  payload: { report_id?: string; export_id?: string; format?: string; run_id?: string; model_version?: string; manifest_sha256?: string; weight_sha256?: string; parse_id?: string; version_id?: string; snapshot_id?: string; answer_id?: string; source_sha256?: string; config_sha256?: string; mode?: 'native' | 'vision' }
  request_id: string
  attempt_count: number
  max_attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  deadline_at: string
  heartbeat_at: string | null
  lease_expires_at: string | null
  cancel_requested_at: string | null
  result: { model_version?: string; completed_epochs?: number; best_epoch?: number; verified?: boolean;
    checks?: string[]; manifest_sha256?: string; weight_sha256?: string;
    files?: { file: string; size_bytes: number; sha256: string }[]; device?: string; limitation?: string;
    parse_id?: string; version_id?: string; page_count?: number; chunk_count?: number; result_sha256?: string;
    snapshot_id?: string; answer_id?: string; answer_status?: 'answered' | 'refused'; citation_count?: number; dimension?: number; tokenizer?: string; review_status?: 'pending'; knowledge_status?: 'unpublished' | 'published' } | null
  error: { code: string; message: string } | null
  attempts: JobAttempt[]
  replayed?: boolean
}

/** 原文上传成功与解析、知识发布采用不同状态。 */
export interface SourceMetadata {
  title: string
  author_or_organization: string
  publication_date: string | null
  equipment_type: string
  applicable_scope: string
  source_url: string | null
  source_kind: 'original' | 'project_summary' | 'simulated_sop'
  license: string
  usage_boundary: string
  redistribution_allowed: boolean
}
export interface DocumentVersion {
  version_id: string
  document_id: string
  number: number
  source_metadata: SourceMetadata
  effective_source_metadata: SourceMetadata
  metadata_revision: number
  management_revision: number
  is_active: boolean
  filename: string
  file_format: 'pdf' | 'md' | 'txt'
  size_bytes: number
  sha256: string
  status: 'uploading' | 'uploaded' | 'failed'
  attempt_count: number
  max_attempts: number
  created_at: string
  uploaded_at: string | null
  lease_expires_at: string | null
  error: { code: string; message: string } | null
  parse_status: 'not_started' | JobStatus
  latest_parse: DocumentParse | null
  knowledge_status: 'unpublished' | 'published' | 'withdrawn'
}
export interface DocumentSummary {
  category: string
  archived_at: string | null
  published_version: KnowledgeSnapshot['members'][number] | null
  available: boolean
  stage: string
  description: string
  document_id: string
  title: string
  created_at: string
  latest_version: DocumentVersion
}
export interface DocumentDetail {
  category: string
  archived_at: string | null
  document_id: string
  title: string
  created_at: string
  latest_uploaded_version_id: string | null
  versions: Page<DocumentVersion>
}
export interface DocumentUpload extends DocumentVersion { replayed: boolean; deduplicated: boolean }

export interface DocumentParse {
  parse_id: string
  version_id: string
  job_id: string
  mode: 'native' | 'vision'
  status: JobStatus
  stage: string
  progress: number
  page_count: number | null
  chunk_count: number | null
  created_at: string
  completed_at: string | null
  source_sha256: string
  result_sha256: string | null
  config_sha256: string
  tokenizer: string
  chunk_tokens: number
  overlap_tokens: number
  model: string | null
  attempt_count: number
  error: { code: string; message: string } | null
  review_status: 'not_ready' | 'pending' | 'approved' | 'rejected' | 'stale' | 'inactive'
  review: ManagementAction | null
  knowledge_status: 'unpublished' | 'published' | 'withdrawn'
  replayed?: boolean
  deduplicated?: boolean
}

export interface ParsedPage {
  page_id: string
  page: number
  text: string
  char_start: number
  char_end: number
  extraction: { blocks: { kind: 'text' | 'heading' | 'table'; text?: string; rows: (string | null)[][]; bbox?: number[] }[]; warnings: string[]; adapter: string; model: string | null; usage: unknown }
}
export interface ParsedChunk {
  chunk_id: string
  ordinal: number
  text: string
  token_count: number
  sha256: string
  page: number
  page_spans: { page: number; char_start: number; char_end: number; chunk_start: number; chunk_end: number; line_start: number; line_end: number }[]
  char_start: number
  char_end: number
  section: string
  language: string
}
export interface ManagementRequest { expected_revision: number; idempotency_key: string; reason: string }
export interface ReviewRequest extends ManagementRequest {
  decision: 'approved' | 'rejected'
  metadata_revision: number
  result_sha256: string
  source_checked: boolean
  content_checked: boolean
  location_checked: boolean
}
export interface ManagementResult {
  action_id: string
  version_id: string
  kind: 'metadata' | 'deactivate' | 'review'
  decision: 'approved' | 'rejected' | null
  management_revision: number
  metadata_revision: number
  is_active: boolean
  knowledge_status: 'unpublished' | 'published' | 'withdrawn'
  replayed?: boolean
}
export interface ManagementAction {
  action_id: string
  kind: ManagementResult['kind']
  decision: ManagementResult['decision']
  metadata_revision: number
  actor: string
  created_at: string
  payload: { reason: string; before?: SourceMetadata; after?: SourceMetadata; source_metadata?: SourceMetadata }
  result: ManagementResult
}
export interface ManagementHistory extends Page<ManagementAction> {
  is_active: boolean
  management_revision: number
  metadata_revision: number
  effective_source_metadata: SourceMetadata
}

export interface KnowledgeSnapshot {
  job_deleted: boolean
  snapshot_id: string
  owner_id: string
  base_snapshot_id: string | null
  job_id: string
  status: JobStatus
  stage: string
  progress: number
  created_at: string
  published_at: string | null
  is_current: boolean
  model: string
  dimension: number
  config_sha256: string
  manifest_sha256: string | null
  chunk_count: number | null
  members: { document_id: string; version_id: string; version_number: number; parse_id: string; review_id: string;
    available?: boolean;
    metadata_revision: number; source_metadata: SourceMetadata; source_sha256: string; result_sha256: string; chunk_count: number }[]
  usage: { request_id: string | null; text_type: string; usage: Record<string, number> | null }[] | null
  error: { code: string; message: string } | null
  replayed?: boolean
}
export interface PublishKnowledgeRequest { parse_id: string; expected_revision: number; idempotency_key: string }
export interface SnapshotPage extends Page<KnowledgeSnapshot> { current: KnowledgeSnapshot | null }
export interface SearchPreview {
  snapshot_id: string
  items: { chunk_id: string; version_id: string; text: string; source_metadata: SourceMetadata;
    source_id: string; parse_id: string; document_version: number; page: number; page_spans: ParsedChunk['page_spans'];
    section: string; language: string; char_start: number; char_end: number; rrf_score: number;
    ranks: { vector?: number; bm25?: number }; cosine_distance?: number; bm25_score?: number }[]
  query_usage: { usage: Record<string, number> | null } | null
  reason?: string
  limitation?: string
}

export interface QuestionRequest { query: string; snapshot_id: string | null; equipment_type: string | null; idempotency_key: string; conversation_id?: string; expected_revision?: number }
export interface ConversationRequest { title: string; snapshot_id: string | null; equipment_type: string | null; engine_key: string | null; end_cycle: number | null; idempotency_key: string }
export interface KnowledgeConversation {
  deleted_at?: string | null
  conversation_id: string; title: string; snapshot_id: string; equipment_type: string | null; revision: number;
  device_context: { engine_key: string; end_cycle: number; dataset_id: string; data_version: string } | null;
  config_snapshot: Record<string, unknown>; config_sha256: string; created_at: string; replayed?: boolean;
}
export interface ConversationDetail extends KnowledgeConversation { turns: KnowledgeAnswer[] }
export interface AnswerCitation {
  evidence_id: string; quote: string; chunk_id: string; source_id: string; version_id: string; parse_id: string;
  document_version: number; source_metadata: SourceMetadata; section: string; char_start: number; char_end: number;
  page_spans: { page: number; char_start: number; char_end: number }[]
}
export interface CitationReference { answer_id: string; claim_index: number; citation_index: number }
export interface CitationSource {
  relation_status?: string; relation_revision?: number; relation_usable?: boolean; saved_revision?: number
  is_archived?: boolean
  citation: AnswerCitation; filename: string; file_format: string; is_active: boolean; review_status: string;
  management_revision: number;
  contexts: { page: number; char_start: number; char_end: number; before: string; quote: string; after: string }[];
}
export type RankedEvidence = SearchPreview['items'][number] & { rerank_rank: number; rerank_score: number }
export type AnswerEvidence = RankedEvidence & { evidence_id: string }
export interface KnowledgeAnswer {
  answer_id: string; job_id: string; snapshot_id: string; question: string; equipment_type: string | null;
  created_at: string; job_status: JobStatus; stage: string; progress: number; replayed?: boolean;
  config_snapshot: Record<string, unknown>; config_sha256: string;
  conversation_id?: string | null; turn_number?: number | null; context_snapshot?: Record<string, unknown> | null;
  result: { status: 'answered' | 'refused'; claims: { text: string; citations: AnswerCitation[] }[]; reason: string | null;
    evidence: AnswerEvidence[]; support_check: { supported_claims: number[]; question_answered: boolean; device_prediction_claims?: number[] } | null; limitation: string;
    clarification_questions?: string[]; suggestions?: { question: string; topic: string; evidence_ids: string[] }[];
    suggestion_error?: { code: string; message: string } | null; guidance?: 'device_workbench' | null } | null;
  trace: { usage?: unknown[]; retrieval?: SearchPreview['items']; reranked?: RankedEvidence[]; evidence?: AnswerEvidence[];
    rewrite?: { status: string; original_question: string; resolved_question: string; replacements: unknown[]; history_turns: number[]; prompt_sha256: string } };
  error: { code: string; message: string } | null
}

export interface ReportExport { export_id: string; report_id: string; format: 'md' | 'json' | 'pdf'; created_at: string; job: Job; sha256: string | null; size: number | null; can_download: boolean }
export interface MaintenanceTask { task_id: string; report_id: string; title: string; note: string; status: 'pending' | 'in_progress' | 'completed' | 'cancelled'; revision: number; created_at: string; updated_at: string; simulation: true; actions: { revision: number; status: string; note: string; actor_name: string; created_at: string }[] }
