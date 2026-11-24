/** 展示层格式化与枚举文案/配色映射。 */

import type { TaskProgress } from '@/api/types'

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return formatTime(value).slice(0, 10)
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = bytes
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

/** 执行来源：任务页要能看出来"这次是谁排的"，否则历史只是流水账。 */
export const ATTEMPT_SOURCE: Record<string, string> = {
  manual: '手动触发',
  auto_retry: '自动重试',
  downgrade: '降参重试',
  manual_revive: '人工继续',
  skip_batches: '跳过失败批次',
  watchdog: '看护重排',
}

export const SKIP_REASON: Record<string, string> = {
  batch_failed: '失败后人工跳过',
  batch_skipped: '无可抽取内容',
}

/**
 * 错误码兜底表：正常由后端下发文案，这里只保证"后端没给或码未登记"时页面不空白。
 * 与 `backend/app/domain/error_catalog.py` 保持同口径。
 */
export const ERROR_CATALOG: Record<string, { message: string; hint: string }> = {
  llm_timeout: { message: '模型调用超时', hint: '稍后继续执行，或降低批大小后重试' },
  llm_unavailable: { message: '模型服务不可用', hint: '检查模型服务状态、网络与额度后继续执行' },
  llm_rejected: { message: '模型拒绝请求', hint: '检查 API Key 与模型名配置，修正后重新触发' },
  neo4j_unavailable: { message: '图库写入失败', hint: '检查图库连接后继续执行，已完成的批次不会重跑' },
  parse_error: { message: '解析失败', hint: '检查文档格式与解析引擎配置，修正后继续执行' },
  bad_input: { message: '输入不可处理', hint: '检查文档格式与解析结果，必要时重新解析' },
  ontology_missing: { message: '本体已变更', hint: '触发时冻结的本体实体类已失效，请重新触发抽取' },
  task_timeout: { message: '执行超时', hint: '从断点继续执行，已完成的批次会保留' },
  attempts_exhausted: { message: '重试预算耗尽', hint: '继续执行、跳过失败批次，或修改输入后重新触发' },
  cancelled: { message: '任务已取消', hint: '需要时可以从断点继续执行' },
  internal_error: { message: '内部错误', hint: '查看服务日志定位原因，修复后继续执行' },
}

export interface ErrorText {
  message: string
  hint: string
  /** 保留原始错误码：未知码也要能看见码本身 */
  code: string | null
}

/** 未知错误码的兜底文案，始终保留原始码。 */
export function errorText(
  code: string | null | undefined,
  message?: string | null,
  hint?: string | null,
): ErrorText {
  const known = code ? ERROR_CATALOG[code] : undefined
  const fallbackMessage = known?.message ?? (code ? '未知错误' : '执行失败')
  return {
    message: message?.trim() ? message : fallbackMessage,
    hint: hint?.trim() ? hint : (known?.hint ?? (code ? '查看服务日志定位原因' : '')),
    code: code || null,
  }
}

/** 进度百分比：已提交与已跳过都算"处理过"，失败时也能看出走了多远。 */
export function progressPercent(progress: TaskProgress | null | undefined): number {
  if (!progress || !progress.batches_total) return 0
  const handled = progress.batches_done + progress.batches_skipped
  return Math.min(100, Math.round((handled / progress.batches_total) * 100))
}

const AUTO_SOURCES = new Set(['auto_retry', 'downgrade', 'watchdog'])

export function isAutoSource(source: string | null | undefined): boolean {
  return !!source && AUTO_SOURCES.has(source)
}

/**
 * PENDING 且由系统重新排队时，状态应显示"重试中"而不是红色失败。
 * `attempt` 是已开始的执行次数，所以即将执行的是第 `attempt + 1` 次。
 */
export function retryStatus(task: {
  status: string
  attempt: number
  next_source: string | null | undefined
}): StatusMeta | null {
  if (task.status !== 'pending' || !isAutoSource(task.next_source)) return null
  const round = Math.max(1, (task.attempt ?? 0) + 1)
  if (task.next_source === 'downgrade') {
    return { label: `降参重试中（第 ${round} 次）`, tone: 'warning' }
  }
  if (task.next_source === 'watchdog') {
    return { label: `看护恢复中（第 ${round} 次）`, tone: 'warning' }
  }
  return { label: `重试中（第 ${round} 次）`, tone: 'warning' }
}

export function skipReasonLabel(reason: string): string {
  return SKIP_REASON[reason] ?? reason
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60_000)} 分 ${Math.round((ms % 60_000) / 1000)} 秒`
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'primary'

interface StatusMeta {
  label: string
  tone: Tone
}

export const DOCUMENT_STATUS: Record<string, StatusMeta> = {
  draft: { label: '草稿', tone: 'info' },
  pending: { label: '审批中', tone: 'warning' },
  published: { label: '已发布', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
  archived: { label: '已归档', tone: 'info' },
}

export const TASK_STATUS: Record<string, StatusMeta> = {
  pending: { label: '排队中', tone: 'info' },
  running: { label: '执行中', tone: 'primary' },
  succeeded: { label: '成功', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'info' },
}

export const ENTITY_STATUS: Record<string, StatusMeta> = {
  proposed: { label: '待审核', tone: 'warning' },
  confirmed: { label: '已确认', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
  merged: { label: '已合并', tone: 'info' },
}

export const RELATION_STATUS: Record<string, StatusMeta> = {
  proposed: { label: '待审核', tone: 'warning' },
  confirmed: { label: '已确认', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
}

export const SENSITIVITY: Record<string, StatusMeta> = {
  public: { label: '公开', tone: 'info' },
  internal: { label: '内部', tone: 'primary' },
  confidential: { label: '机密', tone: 'warning' },
  secret: { label: '绝密', tone: 'danger' },
}

export const REVIEW_STATUS: Record<string, StatusMeta> = {
  pending: { label: '审批中', tone: 'warning' },
  approved: { label: '已通过', tone: 'success' },
  rejected: { label: '已驳回', tone: 'danger' },
  withdrawn: { label: '已撤回', tone: 'info' },
}

export const REVIEW_ACTION: Record<string, StatusMeta> = {
  approve: { label: '通过', tone: 'success' },
  reject: { label: '驳回', tone: 'danger' },
  withdraw: { label: '撤回', tone: 'info' },
}

/** 低置信判定原因；前三个是硬信号，后三个只用于辅助排序与筛选。 */
export const QA_REASON: Record<string, StatusMeta> = {
  no_hit: { label: '无命中', tone: 'danger' },
  no_citation: { label: '无引用', tone: 'danger' },
  user_dislike: { label: '用户不认可', tone: 'warning' },
  few_hits: { label: '命中偏少', tone: 'info' },
  low_score: { label: '匹配度低', tone: 'info' },
  score_gap: { label: '候选分差小', tone: 'info' },
}

export const QA_DISLIKE_REASON: Record<string, string> = {
  off_topic: '答非所问',
  fake_citation: '引用不实',
  outdated: '内容过时',
  incomplete: '回答不完整',
  other: '其他原因',
}

export const QA_ISSUE_STATUS: Record<string, StatusMeta> = {
  pending: { label: '待处置', tone: 'warning' },
  closed: { label: '已关闭', tone: 'info' },
}

export const QA_DISPOSITION: Record<string, string> = {
  false_positive: '误报',
  knowledge_gap: '知识缺口',
  retrieval_failure: '检索失败',
  answer_error: '回答错误',
}

export const QA_ISSUE_ACTION: Record<string, StatusMeta> = {
  assign: { label: '指派', tone: 'primary' },
  dispose: { label: '处置结论', tone: 'warning' },
  close: { label: '关闭', tone: 'info' },
  reopen: { label: '重开', tone: 'success' },
}

export const DOC_TYPE: Record<string, string> = {
  pdf: 'PDF',
  office: 'Office',
  image: '图片',
  text: '文本',
  html: 'HTML',
}

export const ENTITY_CLASS_LABELS: Record<string, string> = {
  person: '人物',
  org: '组织',
  organization: '组织',
  location: '地点',
  project: '项目',
  product: '产品',
  event: '事件',
  concept: '概念',
}

export function statusMeta(map: Record<string, StatusMeta>, key: string | null | undefined): StatusMeta {
  if (!key) return { label: '—', tone: 'info' }
  return map[key] ?? { label: key, tone: 'info' }
}

export function classLabel(code: string): string {
  return ENTITY_CLASS_LABELS[code] ?? code
}

export function tagType(tone: Tone): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  return tone
}
