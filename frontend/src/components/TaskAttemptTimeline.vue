<script setup lang="ts">
/** 执行历史：解析与抽取共用，只负责展示"第几次、谁排的、为什么失败"。 */
import { computed } from 'vue'

import {
  ATTEMPT_SOURCE,
  TASK_STATUS,
  errorText,
  formatDuration,
  formatTime,
  statusMeta,
} from '@/utils/format'

interface AttemptLike {
  id: string
  attempt_no: number
  status: string
  source: string | null
  stats?: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  error_detail: Record<string, unknown> | null
  duration_ms: number | null
  started_at: string | null
  finished_at: string | null
}

const props = defineProps<{ attempts: AttemptLike[] }>()

const rows = computed(() =>
  props.attempts.map((item) => ({
    ...item,
    sourceLabel: item.source ? (ATTEMPT_SOURCE[item.source] ?? item.source) : '—',
    meta: statusMeta(TASK_STATUS, item.status),
    cause: resolveCause(item),
    extras: resolveExtras(item),
    code: item.error_code,
    location: resolveLocation(item),
    raw: resolveRaw(item),
  })),
)

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

/** 失败原因优先给"谁报的、报了什么"，再兜底到原始异常文案；未知码用目录兜底。 */
function resolveCause(item: AttemptLike): string {
  const detail = asRecord(item.error_detail)
  const cause = typeof detail.cause === 'string' ? detail.cause : ''
  const stage = typeof detail.stage === 'string' ? detail.stage : ''
  const text = errorText(item.error_code, item.error_message, null)
  const reason = cause ? errorText(cause).message : text.message
  const parts = [stage && `阶段 ${stage}`, reason].filter(Boolean)
  return item.error_code || item.error_message ? parts.join(' · ') : ''
}

/** 失败位置：批次与块序号区间，能定位到"卡在哪"。 */
function resolveLocation(item: AttemptLike): string {
  const detail = asRecord(item.error_detail)
  const batch = typeof detail.batch_index === 'number' ? detail.batch_index : null
  const seqs = Array.isArray(detail.block_seq)
    ? detail.block_seq.filter((value): value is number => typeof value === 'number')
    : []
  const parts: string[] = []
  if (batch !== null) parts.push(`第 ${batch + 1} 批`)
  if (seqs.length === 1) parts.push(`块 seq ${seqs[0]}`)
  if (seqs.length > 1) parts.push(`块 seq ${seqs[0]}-${seqs[seqs.length - 1]}`)
  const resume = typeof detail.resume_from === 'number' ? detail.resume_from : null
  if (resume !== null && batch === null) parts.push(`从第 ${resume + 1} 批继续`)
  return parts.length ? `位置：${parts.join(' · ')}` : ''
}

/** 原始错误保留可展开：文案给所有人看，明细给排查的人看。 */
function resolveRaw(item: AttemptLike): string {
  if (!item.error_message && !item.error_detail) return ''
  const blocks: string[] = []
  if (item.error_code) blocks.push(`错误码：${item.error_code}`)
  if (item.error_message) blocks.push(item.error_message)
  if (item.error_detail) blocks.push(JSON.stringify(item.error_detail, null, 2))
  return blocks.join('\n')
}

function resolveExtras(item: AttemptLike): string[] {
  const extras: string[] = []
  const stats = asRecord(item.stats)
  if (typeof stats.batches_done === 'number') extras.push(`完成 ${stats.batches_done} 批`)
  if (typeof stats.batches_total === 'number') extras.push(`总 ${stats.batches_total} 批`)
  if (typeof stats.batches_skipped === 'number' && stats.batches_skipped > 0) {
    extras.push(`跳过 ${stats.batches_skipped} 批`)
  }
  const detail = asRecord(item.error_detail)
  if (typeof detail.call_attempts === 'number' && detail.call_attempts > 0) {
    extras.push(`单批重试 ${detail.call_attempts} 次`)
  }
  return extras
}
</script>

<template>
  <div class="timeline">
    <div v-for="row in rows" :key="row.id" class="attempt" :class="row.meta.tone">
      <div class="dot" />
      <div class="body">
        <div class="head">
          <span class="no">#{{ row.attempt_no }}</span>
          <el-tag :type="row.meta.tone" size="small" effect="light">{{ row.meta.label }}</el-tag>
          <span class="source">{{ row.sourceLabel }}</span>
          <span class="muted small">{{ formatDuration(row.duration_ms) }}</span>
          <span class="muted small">{{ formatTime(row.started_at) }}</span>
        </div>
        <div v-if="row.cause" class="cause">
          <span>{{ row.cause }}</span>
          <el-tag v-if="row.code" size="small" effect="plain" class="code">{{ row.code }}</el-tag>
        </div>
        <div v-if="row.location" class="location">{{ row.location }}</div>
        <div v-if="row.extras.length" class="extras">
          <el-tag v-for="item in row.extras" :key="item" size="small" effect="plain">{{ item }}</el-tag>
        </div>
        <details v-if="row.raw" class="raw">
          <summary>原始错误</summary>
          <pre>{{ row.raw }}</pre>
        </details>
      </div>
    </div>
    <el-empty v-if="!rows.length" description="还没有执行记录" :image-size="56" />
  </div>
</template>

<style scoped>
.timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.attempt {
  display: grid;
  grid-template-columns: 14px 1fr;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fff;
}

.attempt.failed {
  border-color: #f3c9c5;
  background: #fffbfa;
}

.dot {
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--muted);
}

.attempt.succeeded .dot {
  background: #12b76a;
}

.attempt.failed .dot {
  background: #f04438;
}

.attempt.running .dot {
  background: #2e6bff;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.no {
  font-family: monospace;
  font-size: 12px;
  color: var(--muted);
}

.source {
  font-size: 12px;
  color: var(--ink-soft);
}

.cause {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: #b42318;
  word-break: break-word;
  flex-wrap: wrap;
}

.code {
  font-family: monospace;
}

.location {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ink-soft);
}

.extras {
  display: flex;
  gap: 6px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.raw {
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}

.raw summary {
  cursor: pointer;
  user-select: none;
}

.raw pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 220px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fcfcfd;
  color: var(--ink-soft);
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
