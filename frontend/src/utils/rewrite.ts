/**
 * 改写实验室的展示计算。
 *
 * 只做"把 Trace 翻译成人能读的结论"，不依赖 Vue，也不发请求：
 * 增益的正负、指标对照、决策文案与顶部告警都能被单测直接复算。
 */
import type { RewriteTrace } from '@/api/types'

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'primary'

export interface LabStatusMeta {
  label: string
  tone: Tone
}

/** 五个启用策略与两个仅登记未启用的策略，顺序与后端注册表一致。 */
export interface LabStrategy {
  code: string
  label: string
  enabled: boolean
  hint: string
}

export const LAB_STRATEGIES: LabStrategy[] = [
  { code: 'context', label: '上下文改写', enabled: true, hint: '按对话历史补全省略的主语与限定条件' },
  { code: 'entity', label: '实体标准化', enabled: true, hint: '把别名对齐到图谱中的标准实体名' },
  { code: 'semantic', label: '语义改写', enabled: true, hint: '去掉口语，改写成检索友好的表达' },
  { code: 'decompose', label: '问题拆解', enabled: true, hint: '把复合问题拆成可独立检索的子问题' },
  { code: 'multi', label: 'Multi-Query', enabled: true, hint: '从不同表达角度扩展同一条信息需求' },
  { code: 'stepback', label: 'Step-back', enabled: false, hint: '先抽象成上位问题再检索（当前版本未启用）' },
  { code: 'hyde', label: 'HyDE', enabled: false, hint: '先生成假设性答案再检索（当前版本未启用）' },
]

export const DEFAULT_ALLOWLIST: string[] = LAB_STRATEGIES.filter((item) => item.enabled).map(
  (item) => item.code,
)

export const DECISION_META: Record<string, LabStatusMeta> = {
  execute: { label: '执行', tone: 'success' },
  not_triggered: { label: '未触发', tone: 'info' },
  blocked_by_allowlist: { label: '被允许列表拦截', tone: 'warning' },
  blocked_by_budget: { label: '被预算拦截', tone: 'warning' },
  blocked_by_dependency: { label: '依赖不满足', tone: 'info' },
  unavailable: { label: '未启用', tone: 'info' },
}

export const STEP_STATUS_META: Record<string, LabStatusMeta> = {
  ok: { label: '已改写', tone: 'success' },
  skipped: { label: '跳过', tone: 'info' },
  degraded: { label: '降级', tone: 'warning' },
}

export function decisionMeta(code: string): LabStatusMeta {
  return DECISION_META[code] ?? { label: code, tone: 'info' }
}

export function stepStatusMeta(status: string): LabStatusMeta {
  return STEP_STATUS_META[status] ?? { label: status || '未知', tone: 'info' }
}

/** 相似度保留四位小数：两位会把真实的增益抹平。 */
export function scoreText(value: number | null | undefined): string {
  const number = Number.isFinite(value) ? Number(value) : 0
  return number.toFixed(4)
}

/** 增益带符号；四舍五入后为 0 时不带符号，避免出现 "-0.0000"。 */
export function liftText(lift: number | null | undefined): string {
  const raw = Number.isFinite(lift) ? Number(lift) : 0
  const rounded = Math.round(raw * 10_000) / 10_000
  const sign = rounded > 0 ? '+' : rounded < 0 ? '-' : ''
  return `${sign}${Math.abs(rounded).toFixed(4)}`
}

export function liftTone(lift: number | null | undefined): Tone {
  const raw = Number.isFinite(lift) ? Number(lift) : 0
  if (raw > 0) return 'success'
  if (raw < 0) return 'danger'
  return 'info'
}

export type Trend = 'up' | 'down' | 'flat'

function trendOf(delta: number, executed: boolean): Trend {
  if (!executed) return 'flat'
  const rounded = Math.round(delta * 10_000) / 10_000
  if (rounded > 0) return 'up'
  if (rounded < 0) return 'down'
  return 'flat'
}

export interface ArmView {
  arm: 'baseline' | 'rewritten'
  executed: boolean
  queries: string[]
  top1: number
  aboveTau: number
  docs: number
  hitCount: number
  reason: string
}

const ARM_FALLBACK: Record<'baseline' | 'rewritten', string> = {
  baseline: '对照组没有返回结果',
  rewritten: '改写阶段未执行，只有对照组结果',
}

/** 一路检索有没有真跑过，看查询列表而不是看指标是否为 0。 */
export function armView(trace: RewriteTrace, arm: 'baseline' | 'rewritten'): ArmView {
  const found = trace.arms.find((item) => item.arm === arm)
  if (!found || found.queries.length === 0) {
    return {
      arm,
      executed: false,
      queries: [],
      top1: 0,
      aboveTau: 0,
      docs: 0,
      hitCount: 0,
      reason: ARM_FALLBACK[arm],
    }
  }
  return {
    arm,
    executed: true,
    queries: [...found.queries],
    top1: found.metrics.top1,
    aboveTau: found.metrics.above_tau,
    docs: found.metrics.docs,
    hitCount: found.hit_count,
    reason: '',
  }
}

export interface ComparisonRow {
  key: string
  label: string
  baseline: string
  rewritten: string
  delta: string
  trend: Trend
}

function signedInt(delta: number): string {
  if (delta === 0) return '0'
  return delta > 0 ? `+${delta}` : String(delta)
}

/**
 * 四行对照：未执行的那一路显示"未完成"，不用 0 冒充结果。
 */
export function comparisonRows(trace: RewriteTrace): ComparisonRow[] {
  const base = armView(trace, 'baseline')
  const next = armView(trace, 'rewritten')
  const executed = next.executed

  return [
    {
      key: 'top1',
      label: '最高分',
      baseline: base.executed ? scoreText(base.top1) : '未完成',
      rewritten: executed ? scoreText(next.top1) : '未完成',
      delta: executed ? liftText(next.top1 - base.top1) : '—',
      trend: trendOf(next.top1 - base.top1, executed),
    },
    {
      key: 'above_tau',
      label: '达到阈值片段数',
      baseline: base.executed ? String(base.aboveTau) : '未完成',
      rewritten: executed ? String(next.aboveTau) : '未完成',
      delta: executed ? signedInt(next.aboveTau - base.aboveTau) : '—',
      trend: trendOf(next.aboveTau - base.aboveTau, executed),
    },
    {
      key: 'docs',
      label: '覆盖文档数',
      baseline: base.executed ? String(base.docs) : '未完成',
      rewritten: executed ? String(next.docs) : '未完成',
      delta: executed ? signedInt(next.docs - base.docs) : '—',
      trend: trendOf(next.docs - base.docs, executed),
    },
    {
      key: 'hit_count',
      label: '命中片段数',
      baseline: base.executed ? String(base.hitCount) : '未完成',
      rewritten: executed ? String(next.hitCount) : '未完成',
      delta: executed ? signedInt(next.hitCount - base.hitCount) : '—',
      trend: trendOf(next.hitCount - base.hitCount, executed),
    },
  ]
}

export interface RewriteAlert {
  level: 'info' | 'warning' | 'danger'
  title: string
  detail: string
}

function warningLevel(text: string): RewriteAlert['level'] {
  if (/超时|取消|回退/.test(text)) return 'danger'
  if (/降级|不足|截断|拦截|未执行/.test(text)) return 'warning'
  return 'info'
}

/**
 * 顶部提示：原始告警逐条透出，再补上"表格里看不出来"的结论——
 * 预算截断拦了谁、哪些策略降级、召回是否低置信度。
 */
export function rewriteAlerts(trace: RewriteTrace): RewriteAlert[] {
  const alerts: RewriteAlert[] = trace.warnings.map((text) => ({
    level: warningLevel(text),
    title: text,
    detail: '',
  }))

  const blocked = trace.routing.filter((item) => item.decision === 'blocked_by_budget')
  if (blocked.length > 0) {
    alerts.push({
      level: 'warning',
      title: `预算上限截断：${blocked.length} 个策略未执行`,
      detail: `被拦截的策略：${blocked.map((item) => item.label).join('、')}`,
    })
  }

  const degraded = trace.rewrites.filter((item) => item.status === 'degraded')
  if (trace.degraded && degraded.length > 0) {
    alerts.push({
      level: 'warning',
      title: '本次运行存在降级策略，结果不完整',
      detail: degraded.map((item) => `${item.label}：${item.reason || '无详情'}`).join('；'),
    })
  }

  if (trace.evaluation.verdict !== 'sufficient') {
    alerts.push({
      level: 'warning',
      title: '召回质量不足（低置信度）',
      detail: trace.evaluation.reason || '改写组未达到阈值要求',
    })
  }

  return alerts
}
