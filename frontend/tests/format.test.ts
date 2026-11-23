/**
 * 展示层单测：错误码兜底、进度换算、自动重试状态。
 * 用 Node 内置 test runner 跑，不依赖浏览器。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { errorText, progressPercent, retryStatus } from '../src/utils/format.ts'

test('已知错误码优先用后端下发的文案', () => {
  const text = errorText('llm_timeout', '模型返回 504', '稍后再试')
  assert.equal(text.message, '模型返回 504')
  assert.equal(text.hint, '稍后再试')
  assert.equal(text.code, 'llm_timeout')
})

test('未知错误码用兜底文案且保留原始码', () => {
  const text = errorText('brand_new_code', null, null)
  assert.equal(text.message, '未知错误')
  assert.equal(text.code, 'brand_new_code')
  assert.ok(text.hint.length > 0)
})

test('进度按已提交与已跳过批次数换算', () => {
  assert.equal(
    progressPercent({ batches_total: 250, batches_done: 187, batches_skipped: 13, current_batch: 200 }),
    80,
  )
  assert.equal(progressPercent(null), 0)
})

test('PENDING 且由系统重排时才显示重试中', () => {
  assert.equal(retryStatus({ status: 'pending', attempt: 2, next_source: 'auto_retry' })?.label, '重试中（第 3 次）')
  assert.equal(retryStatus({ status: 'pending', attempt: 1, next_source: 'downgrade' })?.label, '降参重试中（第 2 次）')
  assert.equal(retryStatus({ status: 'pending', attempt: 1, next_source: 'manual_revive' }), null)
  assert.equal(retryStatus({ status: 'failed', attempt: 1, next_source: 'auto_retry' }), null)
})
