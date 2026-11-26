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
  /** 权限编码来自数据库，登录后由 /auth/me 下发 */
  permissions: string[]
  last_login_at: string | null
}

export interface UserItem {
  id: string
  username: string
  display_name: string
  email: string | null
  status: string
  roles: string[]
  department_id: string | null
  department_name: string | null
  last_login_at: string | null
  created_at: string
}

/* ------------------------------ 部门 / 角色 ------------------------------ */

export interface DepartmentItem {
  id: string
  name: string
  parent_id: string | null
  sort_order: number
  user_count: number
}

export interface DepartmentListResponse {
  items: DepartmentItem[]
}

export interface RoleItem {
  id: string
  code: string
  name: string
  description: string | null
  is_system: boolean
  user_count: number
  permissions: string[]
}

export interface RoleListResponse {
  items: RoleItem[]
}

export interface PermissionItem {
  code: string
  name: string
}

export interface PermissionGroup {
  group: string
  items: PermissionItem[]
}

export interface PermissionCatalogResponse {
  groups: PermissionGroup[]
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
  created_by_name: string | null
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
  /** 上传人姓名与当前所属部门，取自人员档案 */
  created_by_name: string | null
  created_by_department_id: string | null
  created_by_department_name: string | null
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
export type ChunkStrategy = 'block_merge' | 'fixed_window' | 'heading_section'

/** 后端解析后的生效切块配置（缺省值已补齐） */
export interface ChunkConfigView {
  strategy: ChunkStrategy
  size: number
  overlap: number
}

/** ALIAS/ROLE 可产出确定项，RELATION 只产出候选 */
export type AliasKind = 'ALIAS' | 'ROLE' | 'RELATION'
export type AliasCardinality = 'ONE' | 'MANY' | 'UNKNOWN'

export interface AliasRuleShapes {
  suffixes: string[]
  wrappers: string[]
  stop_words: string[]
  max_len: number
}

/** 后端解析后的生效别名规则（缺省值已补齐） */
export interface AliasRule {
  name: string
  kind: AliasKind
  triggers: string[]
  shapes: AliasRuleShapes
  target_cardinality: AliasCardinality
  priority: number
  regex: string | null
}

export interface AliasConfigView {
  enabled: boolean
  use_builtin: boolean
  rules: AliasRule[]
}

export interface ParseEngine {
  name: string
  display_name: string
  doc_types: DocType[]
}

export interface ParseStrategy {
  id: string
  strategy_key: string
  version: number
  name: string
  doc_type: DocType
  engine: string
  config: Record<string, unknown>
  chunk: ChunkConfigView
  /** 生效别名配置；历史脏配置为空对象 */
  alias: AliasConfigView
  is_default: boolean
  status: string
  created_at: string
}

export interface ParseAttempt {
  id: string
  attempt_no: number
  status: string
  /** 这次执行是谁排的：manual / auto_retry / manual_revive / skip_batches / watchdog */
  source: string | null
  quality_score: number | null
  stats: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  error_detail: Record<string, unknown> | null
  duration_ms: number | null
  started_at: string | null
  finished_at: string | null
}

export interface TaskErrorInfo {
  code: string
  message: string
  hint: string | null
  retryable: boolean
}

export interface TaskActions {
  can_revive: boolean
  can_skip_failed_batches: boolean
  revive_remaining: number
  auto_retry_remaining: number
}

export interface TaskProgress {
  batches_total: number
  batches_done: number
  batches_skipped: number
  current_batch: number | null
}

export interface TaskSkipReason {
  reason: string
  count: number
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
  /** 对外可读的失败原因（code + 文案 + 建议） */
  error: TaskErrorInfo | null
  auto_retries: number
  downgrade_retries: number
  manual_revives: number
  /** 下一次执行由谁排的：pending 时据此区分「系统重试中」与「人工触发」 */
  next_source: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  block_count: number
}

export interface GlossaryCounts {
  raw: number
  confirmed: number
  candidate: number
  dropped: number
}

export interface GlossaryEntry {
  surface: string
  canonical: string | null
  candidates: string[]
  kind: string
  certainty: 'CONFIRMED' | 'CANDIDATE'
  resolution: string
  rule: string
  priority: number
  block_ids: string[]
  evidence_block_id: string | null
}

export interface GlossaryRuleHit {
  name: string
  kind: string
  priority: number
  source: 'builtin' | 'custom'
}

/** 解析任务详情里的名词典视图；未启用或历史数据为空对象 */
export interface GlossaryView {
  status: string
  builtin_version?: number
  counts: GlossaryCounts
  rules?: GlossaryRuleHit[]
  error?: string
  entries: GlossaryEntry[]
  truncated: boolean
}

export interface ParseTaskDetail extends ParseTask {
  warnings: string[]
  attempts: ParseAttempt[]
  actions: TaskActions
  glossary: Partial<GlossaryView>
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

export type AttributeType = 'string' | 'number' | 'date' | 'boolean' | 'enum' | 'array'

/** 与后端 AttributeDef 一一对应：抽取与人工修正都按这份契约校验。 */
export interface AttributeDef {
  code: string
  name: string
  type: AttributeType
  description: string
  hint: string | null
  examples: string[]
  required: boolean
  enum_values: string[] | null
  items_type: AttributeType | null
}

export interface OntologyClass {
  id: string
  code: string
  name: string
  description: string | null
  parent_id: string | null
  attributes: Record<string, AttributeDef>
  /** 标识属性编码：取到值即按"标识身份"跨文档对齐 */
  identity_keys: string[]
  version: number
  /** 0 有效、1 失效 */
  status: 0 | 1
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
  attributes: Record<string, AttributeDef>
  version: number
  /** 0 有效、1 失效 */
  status: 0 | 1
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
  error: TaskErrorInfo | null
  auto_retries: number
  downgrade_retries: number
  manual_revives: number
  /** 下一次执行由谁排的：pending 时据此区分「系统重试中」与「人工触发」 */
  next_source: string | null
  /** 有批次被跳过：成功也是"部分完成" */
  incomplete: boolean
  progress: TaskProgress
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface ExtractionAttempt {
  id: string
  attempt_no: number
  status: string
  source: string | null
  stats: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  error_detail: Record<string, unknown> | null
  duration_ms: number | null
  started_at: string | null
  finished_at: string | null
}

export interface ExtractionTaskDetail extends ExtractionTask {
  attempts: ExtractionAttempt[]
  actions: TaskActions
  skip_summary: TaskSkipReason[]
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
  /** name = 归一名称身份，identity = 标识属性身份 */
  identity_kind: string | null
  identity_key: string | null
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
  evidence_text: string | null
  evidence_key: string | null
  attributes: Record<string, unknown>
  description: string | null
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
  /** 本次问答的留痕标识；用作反馈接口的入参 */
  qa_record_id: string
}

/* --------------------------- 问答反馈 / 治理 --------------------------- */

export type QaRating = 'like' | 'dislike'
export type QaDislikeReason = 'off_topic' | 'fake_citation' | 'outdated' | 'incomplete' | 'other'
export type QaIssueStatus = 'pending' | 'closed'
export type QaDisposition = 'false_positive' | 'knowledge_gap' | 'retrieval_failure' | 'answer_error'

export interface QaFeedback {
  id: string
  record_id: string
  rating: QaRating
  reason: string | null
  note: string | null
  created_at: string
  updated_at: string
}

/** 没有评价时 `feedback` 为 null */
export interface QaFeedbackResponse {
  feedback: QaFeedback | null
}

export interface QaFeedbackPayload {
  rating: QaRating
  reason?: string | null
  note?: string | null
}

/** 记录里的来源快照，字段与检索返回的引用一致（部分可缺省） */
export interface QaSource {
  index?: number
  chunk_id?: string | null
  document_id?: string | null
  title?: string | null
  page_no?: number | null
  snippet?: string | null
  score?: number | null
  sensitivity?: string | null
}

export interface QaIssue {
  id: string
  representative_question: string
  record_count: number
  dislike_count: number
  reasons: string[]
  sensitivity: Sensitivity
  status: QaIssueStatus
  disposition: QaDisposition | null
  assignee_id: string | null
  assignee_name: string | null
  primary_document_id: string | null
  primary_document_title: string | null
  hit_count: number | null
  first_seen_at: string
  last_seen_at: string
  created_at: string | null
}

export interface QaIssueListResponse {
  items: QaIssue[]
  total: number
  page: number
  page_size: number
}

export interface QaRecordItem {
  id: string
  question: string
  answer_excerpt: string
  answer_status: string
  reasons: string[]
  sensitivity: Sensitivity
  user_name: string | null
  hit_count: number
  top_score: number | null
  sources: QaSource[]
  created_at: string
  finished_at: string | null
}

export interface QaIssueEvent {
  id: string
  action: string
  actor_name: string | null
  note: string | null
  detail: Record<string, unknown>
  created_at: string
}

export interface QaIssueDetail extends QaIssue {
  records: QaRecordItem[]
  events: QaIssueEvent[]
}

export interface QaAssignee {
  id: string
  display_name: string
  username: string
}

/* ------------------------------ 查询改写实验室 ----------------------------- */

export type RewriteMode = 'auto' | 'manual'

/** 与后端路由决策一一对应；`blocked_*` 表示"本该改写但被拦下"。 */
export type StrategyDecision =
  | 'execute'
  | 'not_triggered'
  | 'blocked_by_allowlist'
  | 'blocked_by_budget'
  | 'blocked_by_dependency'
  | 'unavailable'

export interface RewriteHistoryTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface RewriteRunParams {
  query: string
  history?: RewriteHistoryTurn[]
  mode?: RewriteMode
  allowlist?: string[]
  budget?: number
  quality_threshold?: number
  top_k?: number
  doc_type?: DocType
}

export interface RewriteRequestEcho {
  query: string
  mode: RewriteMode
  allowlist: string[]
  budget: number
  quality_threshold: number
  top_k: number
  doc_type: string | null
  history_turns: number
}

export interface RewriteReference {
  cue: string
  resolved: boolean
  reason: string
}

export interface RewriteUnderstanding {
  intent: string
  complexity: string
  entities: string[]
  references: RewriteReference[]
  constraints: string[]
  candidate_strategies: string[]
  source: string
  degraded: boolean
}

export interface RewriteDecision {
  strategy: string
  label: string
  kind: 'text' | 'expand'
  decision: StrategyDecision
  evidence: string[]
  reason: string
}

export interface RewriteNode {
  id: string
  label: string
  kind: string
  depends_on: string[]
  status: string
  elapsed_ms: number
}

export interface RewriteStep {
  strategy: string
  label: string
  status: string
  text_in: string
  text_out: string
  reason: string
}

export interface RewriteArmMetrics {
  top1: number
  above_tau: number
  docs: number
}

export interface RewriteArm {
  arm: 'baseline' | 'rewritten'
  queries: string[]
  metrics: RewriteArmMetrics
  hit_count: number
}

export interface RewriteSecondStage {
  triggered: boolean
  actions: string[]
  new_chunks: number
  reason: string
}

export interface RewriteFusedChunk {
  index: number
  chunk_id: string
  document_id: string
  title: string
  version_no: number
  content: string
  truncated: boolean
  heading_path: string | null
  score: number
  sources: string[]
}

export interface RewriteTrace {
  request: RewriteRequestEcho
  understanding: RewriteUnderstanding
  routing: RewriteDecision[]
  workflow: RewriteNode[]
  rewrites: RewriteStep[]
  arms: RewriteArm[]
  evaluation: { verdict: 'sufficient' | 'insufficient'; lift: number; reason: string }
  second_stage: RewriteSecondStage
  fusion: { method: string; k: number; before: number; after: number; items: RewriteFusedChunk[] }
  totals: { llm_calls: number; embed_calls: number; retrieval_calls: number; elapsed_ms: number }
  degraded: boolean
  warnings: string[]
}
