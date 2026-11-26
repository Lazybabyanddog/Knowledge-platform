<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { documentApi } from '@/api/documents'
import { errorMessage } from '@/api/http'
import { parseApi } from '@/api/parse'
import type { ParseBlock, ParseTask, ParseTaskDetail } from '@/api/types'
import TaskAttemptTimeline from '@/components/TaskAttemptTimeline.vue'
import { TASK_STATUS, formatTime, retryStatus, statusMeta } from '@/utils/format'

const router = useRouter()

const loading = ref(false)
const rows = ref<ParseTask[]>([])
const total = ref(0)
const titleMap = ref<Record<string, string>>({})
const query = reactive({ status: '', page: 1, page_size: 10 })

const detailVisible = ref(false)
const detail = ref<ParseTaskDetail | null>(null)
const blocks = ref<ParseBlock[]>([])
const blocksLoading = ref(false)

const statusOptions = Object.entries(TASK_STATUS)
  .filter(([value]) => ['pending', 'running', 'succeeded', 'failed', 'cancelled'].includes(value))
  .map(([value, meta]) => ({ value, label: meta.label }))

const detailSummary = computed(() => {
  const stats = detail.value?.stats ?? {}
  // 嵌套对象（如 glossary 快照）不适合塞进 tag，过滤掉避免出现 [object Object]
  return Object.fromEntries(
    Object.entries(stats).filter(([, value]) => typeof value !== 'object' || value === null),
  )
})

const glossary = computed(() => {
  const view = detail.value?.glossary
  return view && Object.keys(view).length ? view : null
})

const glossarySummary = computed(() => {
  const view = glossary.value
  if (!view) return ''
  if (view.status === 'degraded') return '名词典抽取失败，已跳过'
  const counts = view.counts
  if (!counts) return '名词典'
  return `名词典 ${counts.confirmed + counts.candidate} 条（确定 ${counts.confirmed} / 候选 ${counts.candidate} / 丢弃 ${counts.dropped}）`
})

async function load(): Promise<void> {
  loading.value = true
  try {
    const [page, documents] = await Promise.all([
      parseApi.listTasks({
        status: query.status || undefined,
        page: query.page,
        page_size: query.page_size,
      }),
      documentApi.list({ page: 1, page_size: 100 }).catch(() => null),
    ])
    rows.value = page.items
    total.value = page.total
    if (documents) {
      titleMap.value = Object.fromEntries(documents.items.map((item) => [item.id, item.title]))
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function search(): void {
  query.page = 1
  void load()
}

async function openDetail(task: ParseTask): Promise<void> {
  detailVisible.value = true
  detail.value = null
  blocks.value = []
  blocksLoading.value = true
  try {
    const [taskDetail, blockPage] = await Promise.all([
      parseApi.taskDetail(task.id),
      parseApi.blocks(task.id, { page: 1, page_size: 200 }),
    ])
    detail.value = taskDetail
    blocks.value = blockPage.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    blocksLoading.value = false
  }
}

async function retry(task: ParseTask): Promise<void> {
  try {
    await parseApi.retry(task.id)
    ElMessage.success('已从断点继续，排队中')
    await load()
    if (detailVisible.value && detail.value?.id === task.id) {
      detail.value = await parseApi.taskDetail(task.id)
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function cancel(task: ParseTask): Promise<void> {
  try {
    await parseApi.cancel(task.id)
    ElMessage.success('已取消')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(task: ParseTask): Promise<void> {
  const hint =
    task.status === 'succeeded'
      ? '解析产物、执行历史以及由此产生的索引片段都会被删除，且不可恢复。'
      : '执行历史与产物会被一并删除，且不可恢复。'
  try {
    await ElMessageBox.confirm(
      `确定删除任务 ${task.id.slice(0, 8)} 吗？${hint}`,
      '删除解析任务',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await parseApi.deleteTask(task.id)
    ElMessage.success('已删除')
    if (detailVisible.value && detail.value?.id === task.id) {
      detailVisible.value = false
    }
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">解析任务</h1>
        <p class="page-sub">查看解析进度、质量评分与逐块产物；失败任务可重试。</p>
      </div>
      <div class="toolbar">
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button type="primary" plain @click="search">刷新</el-button>
      </div>
    </div>

    <div class="card">
      <el-table v-loading="loading" :data="rows" size="default">
        <el-table-column label="任务" width="120">
          <template #default="{ row }: { row: ParseTask }">
            <div class="mono">{{ row.id.slice(0, 8) }}</div>
            <div class="muted small">#{{ row.attempt }}</div>
          </template>
        </el-table-column>

        <el-table-column label="文档" min-width="220">
          <template #default="{ row }: { row: ParseTask }">
            <el-button link type="primary" @click="router.push(`/documents/${row.document_id}`)">
              {{ titleMap[row.document_id] ?? row.document_id.slice(0, 8) }}
            </el-button>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="130">
          <template #default="{ row }: { row: ParseTask }">
            <el-tag v-if="retryStatus(row)" :type="retryStatus(row)?.tone" size="small">
              {{ retryStatus(row)?.label }}
            </el-tag>
            <el-tag v-else :type="statusMeta(TASK_STATUS, row.status).tone" size="small">
              {{ statusMeta(TASK_STATUS, row.status).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="块数" width="80">
          <template #default="{ row }: { row: ParseTask }">{{ row.block_count }}</template>
        </el-table-column>

        <el-table-column label="质量分" width="110">
          <template #default="{ row }: { row: ParseTask }">
            <span v-if="row.quality_score === null" class="muted">—</span>
            <span v-else :class="{ low: row.low_quality }">{{ row.quality_score.toFixed(1) }}</span>
            <el-tag v-if="row.low_quality" size="small" type="warning" style="margin-left: 6px">低</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="创建时间" width="150">
          <template #default="{ row }: { row: ParseTask }">
            <span class="muted small">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="失败原因" min-width="200">
          <template #default="{ row }: { row: ParseTask }">
            <el-tooltip v-if="row.error || row.error_message" placement="top">
              <template #content>
                <div>{{ row.error?.message ?? '执行失败' }}</div>
                <div v-if="row.error?.hint" class="muted small">{{ row.error.hint }}</div>
                <div v-if="row.error_message" class="small">{{ row.error_message }}</div>
              </template>
              <span class="danger-text small ellipsis">
                {{ row.error?.message ?? row.error_message }}
              </span>
            </el-tooltip>
            <span v-else class="muted small">—</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="240" align="right">
          <template #default="{ row }: { row: ParseTask }">
            <el-button link type="primary" @click="openDetail(row)">产物</el-button>
            <el-button
              v-if="row.status === 'failed' || row.status === 'cancelled'"
              link
              type="warning"
              @click="retry(row)"
            >
              继续执行
            </el-button>
            <el-button
              v-if="row.status === 'pending' || row.status === 'running'"
              link
              type="danger"
              @click="cancel(row)"
            >
              取消
            </el-button>
            <el-button
              v-if="row.status !== 'pending' && row.status !== 'running'"
              link
              type="danger"
              @click="remove(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="还没有解析任务" :image-size="70" />
        </template>
      </el-table>

      <div class="card-pad pager">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="load"
          @size-change="search"
        />
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="解析产物" size="62%">
      <div v-loading="blocksLoading" class="drawer-body">
        <template v-if="detail">
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="任务">
              <span class="mono">{{ detail.id.slice(0, 8) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag
                v-if="retryStatus(detail)"
                :type="retryStatus(detail)?.tone"
                size="small"
              >
                {{ retryStatus(detail)?.label }}
              </el-tag>
              <span v-else>{{ statusMeta(TASK_STATUS, detail.status).label }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="质量分">
              {{ detail.quality_score === null ? '—' : detail.quality_score.toFixed(1) }}
            </el-descriptions-item>
            <el-descriptions-item label="块数">{{ detail.block_count }}</el-descriptions-item>
            <el-descriptions-item label="尝试次数">{{ detail.attempt }}</el-descriptions-item>
            <el-descriptions-item label="完成时间">{{ formatTime(detail.finished_at) }}</el-descriptions-item>
          </el-descriptions>

          <div class="budget-row">
            <el-tag size="small" effect="plain">自动重试剩余 {{ detail.actions.auto_retry_remaining }}</el-tag>
            <el-tag size="small" effect="plain">人工继续剩余 {{ detail.actions.revive_remaining }}</el-tag>
            <el-button
              v-if="detail.actions.can_revive"
              size="small"
              type="warning"
              plain
              @click="retry(detail)"
            >
              从断点继续
            </el-button>
          </div>

          <el-alert
            v-if="detail.status === 'failed' && detail.error"
            :title="detail.error.message"
            :description="detail.error.hint ?? detail.error_message ?? ''"
            type="error"
            :closable="false"
            show-icon
          />

          <section class="section">
            <h3 class="section-title">执行历史</h3>
            <TaskAttemptTimeline :attempts="detail.attempts" />
          </section>

          <div v-if="detail.warnings.length" class="warnings">
            <el-alert
              v-for="(warning, index) in detail.warnings"
              :key="index"
              :title="warning"
              type="warning"
              :closable="false"
              show-icon
            />
          </div>

          <div class="stats-row">
            <el-tooltip
              v-if="glossary"
              :disabled="glossary.status !== 'degraded'"
              :content="glossary.error ?? '不影响索引，可重建后再看'"
              placement="top"
            >
              <el-tag
                size="small"
                :type="glossary.status === 'degraded' ? 'warning' : 'success'"
                effect="plain"
              >
                {{ glossarySummary }}
              </el-tag>
            </el-tooltip>
            <el-tag v-for="(value, key) in detailSummary" :key="key" size="small" effect="plain">
              {{ key }}: {{ value }}
            </el-tag>
          </div>

          <div class="blocks">
            <div v-for="block in blocks" :key="block.id" class="block">
              <div class="block-side">
                <span class="seq">#{{ block.seq }}</span>
                <el-tag size="small" effect="plain">{{ block.block_type }}</el-tag>
                <span v-if="block.page_no" class="muted small">p{{ block.page_no }}</span>
              </div>
              <div class="block-content" :class="{ discarded: block.is_discarded }">
                {{ block.content }}
              </div>
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--line);
}

.low {
  color: #b54708;
  font-weight: 600;
}

.danger-text {
  color: #d92d20;
}

.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.warnings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.budget-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-soft);
}

.blocks {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.block {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
}

.block-side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.seq {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
}

.block-content {
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink-soft);
}

.block-content.discarded {
  color: #b0b6c3;
  text-decoration: line-through;
}
</style>
