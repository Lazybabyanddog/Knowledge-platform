/** 与后端 Pydantic 模型一一对应的 DTO 定义。 */

export interface Envelope<T> {
  code: number
  message: string
  data: T
  request_id?: string
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/* ------------------------------- 认证 / 用户 ------------------------------ */

export interface TokenUser {
  id: string
  display_name: string
  roles: string[]
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: TokenUser
}

export interface MeResponse {
  id: string
  username: string
  display_name: string
  email: string | null
  status: string
  workspace_id: string
  roles: string[]
  last_login_at: string | null
}

export interface UserItem {
  id: string
  username: string
  display_name: string
  email: string | null
  status: string
  roles: string[]
  last_login_at: string | null
  created_at: string
}

/* --------------------------------- 文档 --------------------------------- */

export type DocumentStatus = 'draft' | 'pending' | 'published' | 'rejected' | 'archived'
export type Sensitivity = 'public' | 'internal' | 'confidential' | 'secret'

export interface DocumentVersion {
  id: string
  document_id: string
  version_no: number
  file_name: string
  file_size: number
  mime_type: string
  checksum: string
  page_count: number | null
  is_current: boolean
  change_note: string | null
  created_by: string | null
  created_at: string
}

export interface DocumentItem {
  id: string
  title: string
  summary: string | null
  source_type: string
  doc_type: string
  status: DocumentStatus
  current_version_id: string | null
  owner_id: string | null
  sensitivity: Sensitivity
  tags: string[]
  extra_metadata: Record<string, unknown>
  deleted_at: string | null
  created_by: string | null
  created_at: string
  updated_at: string
  current_version: DocumentVersion | null
}

export interface DownloadUrl {
  url: string
  file_name: string
  version_no: number
  expires_in: number
}

export interface BatchUploadItem {
  file_name: string
  success: boolean
  document_id: string | null
  version_id: string | null
  error: { code: number; message: string } | null
}

export interface BatchUploadResponse {
  items: BatchUploadItem[]
  succeeded: number
  failed: number
}

/* --------------------------------- 解析 --------------------------------- */

export type ParseTaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type DocType = 'pdf' | 'office' | 'image' | 'text' | 'html'

export interface ParseStrategy {
  id: string
  strategy_key: string
  version: number
  name: string
  doc_type: DocType
  engine: string
  config: Record<string, unknown>
  is_default: boolean
  status: string
  created_at: string
}

export interface ParseAttempt {
  id: string
  attempt_no: number
  status: string
  quality_score: number | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ParseTask {
  id: string
  document_id: string
  document_version_id: string
  strategy_id: string
  status: ParseTaskStatus
  attempt: number
  quality_score: number | null
  low_quality: boolean
  stats: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  block_count: number
}

export interface ParseTaskDetail extends ParseTask {
  warnings: string[]
  attempts: ParseAttempt[]
}

export interface ParseBlock {
  id: string
  seq: number
  block_type: string
  content: string
  page_no: number | null
  bbox: Record<string, unknown> | null
  level: number | null
  parent_block_id: string | null
  is_discarded: boolean
  metadata: Record<string, unknown>
}

/* --------------------------------- 本体 --------------------------------- */

export interface OntologyClass {
  id: string
  code: string
  name: string
  description: string | null
  parent_id: string | null
  attributes: Record<string, unknown>
  version: number
  status: string
  created_at: string
  updated_at: string
}

export interface OntologyRelation {
  id: string
  code: string
  name: string
  description: string | null
  source_class_id: string
  target_class_id: string
  source_class_code: string
  target_class_code: string
  version: number
  status: string
  created_at: string
  updated_at: string
}

/* ------------------------------ 抽取 / 图谱 ------------------------------ */

export type EntityStatus = 'proposed' | 'confirmed' | 'rejected' | 'merged'
export type RelationStatus = 'proposed' | 'confirmed' | 'rejected'
export type ExtractionStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface ExtractionTask {
  id: string
  document_id: string
  document_version_id: string
  parse_task_id: string
  ontology_version: number
  status: ExtractionStatus
  attempt: number
  stats: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface EntityItem {
  id: string
  class_code: string
  name: string
  aliases: string[]
  description: string | null
  status: EntityStatus
  confidence: number
  merged_into: string | null
  reject_reason: string | null
  extraction_task_id: string | null
  document_id: string | null
  document_ids: string[]
  evidence_block_ids: string[]
  attributes: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface RelationItem {
  id: string
  code: string
  source_id: string
  target_id: string
  confidence: number
  status: RelationStatus
  evidence_block_id: string | null
  document_id: string | null
  extraction_task_id: string | null
  reject_reason: string | null
  created_at: string | null
}

export interface SubgraphEdge {
  id: string
  code: string
  source_id: string
  target_id: string
  confidence: number
  status: string
}

export interface Subgraph {
  nodes: EntityItem[]
  edges: SubgraphEdge[]
  depth: number
  truncated: boolean
}

export interface EntitySource {
  entity_id: string
  document_ids: string[]
  blocks: Array<{
    block_id: string
    document_id: string
    page_no: number | null
    snippet: string
  }>
}

export interface MergeResult {
  merge_id: string
  target_id: string
  source_ids: string[]
  merged_count: number
  undo_deadline: string
}

/* --------------------------------- 审批 --------------------------------- */

export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn'
export type ReviewAction = 'approve' | 'reject' | 'withdraw'
export type ApproverType = 'role' | 'user'

export interface ReviewStep {
  step_no: number
  name: string
  approver_type: ApproverType
  approver_value: string
  skippable: boolean
}

export type ReviewStepInput = Omit<ReviewStep, 'step_no'>

export interface ReviewFlow {
  id: string
  name: string
  description: string | null
  doc_type: string
  steps: ReviewStep[]
  is_default: boolean
  status: string
  created_at: string
  updated_at: string
}

export interface ReviewFlowPage {
  items: ReviewFlow[]
  total: number
}

export interface ReviewRecord {
  id: string
  step_no: number
  step_name: string | null
  action: ReviewAction
  actor_id: string | null
  actor_name: string | null
  comment: string | null
  created_at: string
}

export interface ReviewInstance {
  id: string
  document_id: string
  document_title: string
  doc_type: string
  version_no: number
  flow_id: string | null
  flow_name: string
  status: ReviewStatus
  current_step: number
  total_steps: number
  current_step_name: string | null
  submitted_by: string | null
  submitter_name: string | null
  submitted_at: string
  finished_at: string | null
  step_started_at: string | null
  waiting_workdays: number
  overdue: boolean
}

export interface ReviewInstanceDetail extends ReviewInstance {
  steps: ReviewStep[]
  records: ReviewRecord[]
}

/* ------------------------------- 检索 / 问答 ------------------------------ */

/** 一条引用片段；``index`` 与答案中的 [1][2] 标注对应。 */
export interface RetrievalSource {
  index: number
  chunk_id: string
  document_id: string
  title: string
  version_no: number
  page_no: number | null
  page_range: number[] | null
  heading_path: string | null
  score: number
  snippet: string
}

export interface RetrievalSearchResponse {
  items: RetrievalSource[]
  total: number
}

export interface RetrievalSearchParams {
  query: string
  top_k?: number
  doc_type?: DocType
}

export interface AskParams {
  question: string
  top_k?: number
  doc_type?: DocType
}

export interface AskDone {
  elapsed_ms: number
  sources: number
}
