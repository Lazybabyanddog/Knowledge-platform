/** 展示层格式化与枚举文案/配色映射。 */

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
