/**
 * 改写实验室展示计算单测：策略清单、决策文案、增益正负、双路对照与顶部告警。
 * 这些都是纯函数，用 Node 内置 test runner 直接跑，不需要浏览器。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { RewriteArm, RewriteTrace } from '../src/api/types.ts'
import {
  DEFAULT_ALLOWLIST,
  LAB_STRATEGIES,
  armView,
  comparisonRows,
  decisionMeta,
  liftText,
  liftTone,
  rewriteAlerts,
  scoreText,
  stepStatusMeta,
} from '../src/utils/rewrite.ts'

function arm(overrides: Partial<RewriteArm> = {}): RewriteArm {
  return {
    arm: 'baseline',
    queries: ['原始问题'],
    metrics: { top1: 0.82, above_tau: 3, docs: 2 },
    hit_count: 5,
    ...overrides,
  }
}

function makeTrace(overrides: Partial<RewriteTrace> = {}): RewriteTrace {
  return {
    request: {
      query: '年假怎么算？',
      mode: 'auto',
      allowlist: [...DEFAULT_ALLOWLIST],
      budget: 3,
      quality_threshold: 0.6,
      top_k: 5,
      doc_type: null,
      history_turns: 0,
    },
    understanding: {
      intent: 'lookup',
      complexity: 'simple',
      entities: [],
      references: [],
      constraints: [],
      candidate_strategies: [],
      source: 'rules',
      degraded: false,
    },
    routing: [],
    workflow: [],
    rewrites: [],
    arms: [arm(), arm({ arm: 'rewritten', queries: ['年假 计算 规则'] })],
    evaluation: { verdict: 'sufficient', lift: 0.04, reason: '达到阈值' },
    second_stage: { triggered: false, actions: [], new_chunks: 0, reason: '' },
    fusion: { method: 'rrf', k: 60, before: 0, after: 0, items: [] },
    totals: { llm_calls: 3, embed_calls: 2, retrieval_calls: 3, elapsed_ms: 1200 },
    degraded: false,
    warnings: [],
    ...overrides,
  }
}

test('注册表含 7 个策略，默认允许列表只开五个', () => {
  assert.equal(LAB_STRATEGIES.length, 7)
  assert.equal(LAB_STRATEGIES.filter((item) => item.enabled).length, 5)
  assert.deepEqual(DEFAULT_ALLOWLIST, ['context', 'entity', 'semantic', 'decompose', 'multi'])
  assert.deepEqual(
    LAB_STRATEGIES.filter((item) => !item.enabled).map((item) => item.code),
    ['stepback', 'hyde'],
  )
})

test('决策文案覆盖五类结果，未知码保留原码', () => {
  assert.equal(decisionMeta('execute').label, '执行')
  assert.equal(decisionMeta('not_triggered').label, '未触发')
  assert.equal(decisionMeta('blocked_by_allowlist').label, '被允许列表拦截')
  assert.equal(decisionMeta('blocked_by_budget').label, '被预算拦截')
  assert.equal(decisionMeta('unavailable').label, '未启用')
  assert.equal(decisionMeta('brand_new').label, 'brand_new')
  assert.equal(stepStatusMeta('degraded').tone, 'warning')
  assert.equal(stepStatusMeta('weird').label, 'weird')
})

test('增益带符号且不出现负零', () => {
  assert.equal(liftText(0.042), '+0.0420')
  assert.equal(liftText(-0.031), '-0.0310')
  assert.equal(liftText(0), '0.0000')
  assert.equal(liftText(-0.00004), '0.0000')
  assert.equal(liftText(Number.NaN), '0.0000')
  assert.equal(liftTone(0.01), 'success')
  assert.equal(liftTone(-0.01), 'danger')
  assert.equal(liftTone(0), 'info')
  assert.equal(scoreText(0.81234), '0.8123')
})

test('某一路没有查询时视为未执行，不用指标冒充结果', () => {
  const stale = makeTrace({
    arms: [arm(), arm({ arm: 'rewritten', queries: [], metrics: { top1: 0.99, above_tau: 9, docs: 9 } })],
  })
  const view = armView(stale, 'rewritten')
  assert.equal(view.executed, false)
  assert.equal(view.top1, 0)
  assert.ok(view.reason.includes('只有对照组'))

  const missing = armView(makeTrace({ arms: [] }), 'baseline')
  assert.equal(missing.executed, false)
  assert.ok(missing.reason.includes('没有返回结果'))
})

test('对照表给出两路数值与差值方向', () => {
  const rows = comparisonRows(makeTrace())
  const top1 = rows.find((row) => row.key === 'top1')
  assert.deepEqual(
    { baseline: top1?.baseline, rewritten: top1?.rewritten, delta: top1?.delta, trend: top1?.trend },
    { baseline: '0.8200', rewritten: '0.8200', delta: '0.0000', trend: 'flat' },
  )

  const worse = comparisonRows(
    makeTrace({
      arms: [
        arm(),
        arm({
          arm: 'rewritten',
          queries: ['改写后'],
          metrics: { top1: 0.61, above_tau: 1, docs: 1 },
          hit_count: 3,
        }),
      ],
    }),
  )
  const worseTop1 = worse.find((row) => row.key === 'top1')
  assert.equal(worseTop1?.delta, '-0.2100')
  assert.equal(worseTop1?.trend, 'down')
  assert.equal(worse.find((row) => row.key === 'above_tau')?.delta, '-2')
  assert.equal(worse.find((row) => row.key === 'hit_count')?.delta, '-2')
})

test('改写组未执行时对照表显示未完成而不是 0', () => {
  const rows = comparisonRows(
    makeTrace({ arms: [arm(), arm({ arm: 'rewritten', queries: [] })] }),
  )
  for (const row of rows) {
    assert.equal(row.rewritten, '未完成')
    assert.equal(row.delta, '—')
    assert.equal(row.trend, 'flat')
  }
})

test('超时、预算截断与低置信度都在顶部出提示', () => {
  const trace = makeTrace({
    warnings: [
      '运行超时，上下文改写及后续策略未执行',
      '预算不足，改写组只检索前 2 条查询',
      '未配置 LLM，只执行规则理解与对照组检索',
    ],
    routing: [
      { strategy: 'multi', label: 'Multi-Query', kind: 'expand', decision: 'blocked_by_budget', evidence: [], reason: '' },
    ],
    rewrites: [
      { strategy: 'semantic', label: '语义改写', status: 'degraded', text_in: 'a', text_out: 'a', reason: '模型超时' },
    ],
    degraded: true,
    evaluation: { verdict: 'insufficient', lift: -0.02, reason: '最高分 0.51 低于阈值 0.6' },
  })
  const alerts = rewriteAlerts(trace)
  const levels = alerts.map((item) => item.level)
  assert.deepEqual(levels.slice(0, 3), ['danger', 'warning', 'info'])
  assert.ok(alerts.some((item) => item.title.includes('预算上限截断：1 个策略未执行')))
  assert.ok(alerts.some((item) => item.detail.includes('Multi-Query')))
  assert.ok(alerts.some((item) => item.title.includes('降级策略')))
  assert.ok(alerts.some((item) => item.title.includes('低置信度')))
  assert.ok(alerts.some((item) => item.detail.includes('最高分 0.51')))

  const calm = rewriteAlerts(makeTrace())
  assert.deepEqual(calm, [])
})
