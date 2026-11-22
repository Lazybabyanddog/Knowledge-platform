<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { documentApi } from '@/api/documents'
import { errorMessage } from '@/api/http'
import { knowledgeApi } from '@/api/knowledge'
import { parseApi } from '@/api/parse'
import { reviewApi } from '@/api/reviews'
import type {
  DocumentItem,
  DocumentVersion,
  EntityItem,
  ExtractionTask,
  ParseBlock,
  ParseTask,
  ReviewFlow,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import {
  DOCUMENT_STATUS,
  DOC_TYPE,
  ENTITY_STATUS,
  SENSITIVITY,
  TASK_STATUS,
  classLabel,
  formatFileSize,
  formatTime,
  statusMeta,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const documentId = String(route.params.id)
const loading = ref(true)
const doc = ref<DocumentItem | null>(null)
const versions = ref<DocumentVersion[]>([])
const parseTasks = ref<ParseTask[]>([])
const extractions = ref<ExtractionTask[]>([])
const entities = ref<EntityItem[]>([])
const blocks = ref<ParseBlock[]>([])
const blocksTaskId = ref<string>('')
const blocksQuery = reactive({ page: 1, page_size: 20, total: 0 })
const activeTab = ref('overview')
const busy = ref('')

const editVisible = ref(false)
const editForm = reactive({ title: '', summary: '', sensitivity: 'internal', tags: '' })

const versionVisible = ref(false)
const versionFile = ref<File | null>(null)
const versionNote = ref('')

let timer: number | undefined

const latestParse = computed(
  () => parseTasks.value.find((item) => item.status === 'succeeded') ?? null,
)
const currentVersion = computed(() => doc.value?.current_version ?? null)
const running = computed(
  () =>
    parseTasks.value.some((item) => item.status === 'running' || item.status === 'pending') ||
    extractions.value.some((item) => item.status === 'running' || item.status === 'pending'),
)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [detail, versionRows] = await Promise.all([
      documentApi.detail(documentId),
      documentApi.versions(documentId).catch(() => []),
    ])
    doc.value = detail
    versions.value = versionRows
    editForm.title = detail.title
    editForm.summary = detail.summary ?? ''
    editForm.sensitivity = detail.sensitivity
    editForm.tags = detail.tags.join(', ')
    await loadTasks()
    await loadEntities()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadTasks(): Promise<void> {
  const [parsePage, extractionPage] = await Promise.all([
    parseApi.listTasks({ document_id: documentId, page: 1, page_size: 20 }),
    knowledgeApi.listExtractions({ document_id: documentId, page: 1, page_size: 20 }),
  ])
  parseTasks.value = parsePage.items
  extractions.value = extractionPage.items
}

async function loadEntities(): Promise<void> {
  try {
    const page = await knowledgeApi.documentEntities(documentId, { page: 1, page_size: 50 })
    entities.value = page.items
  } catch {
    entities.value = []
  }
}

async function loadBlocks(taskId: string, page = 1): Promise<void> {
  try {
    const data = await parseApi.blocks(taskId, { page, page_size: blocksQuery.page_size })
    blocks.value = data.items
    blocksTaskId.value = taskId
    blocksQuery.page = data.page
    blocksQuery.total = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function onBlocksPageChange(page: number): void {
  if (blocksTaskId.value) void loadBlocks(blocksTaskId.value, page)
}

async function onTabChange(name: string): Promise<void> {
  if (name === 'blocks' && latestParse.value) {
    await loadBlocks(latestParse.value.id)
  }
}

async function triggerParse(): Promise<void> {
  if (!currentVersion.value) {
    ElMessage.warning('当前文档没有版本，无法解析')
    return
  }
  busy.value = 'parse'
  try {
    const task = await parseApi.trigger({ document_version_id: currentVersion.value.id })
    ElMessage.success(`已提交解析任务 ${task.id.slice(0, 8)}`)
    await loadTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    busy.value = ''
  }
}

async function triggerExtraction(): Promise<void> {
  if (!latestParse.value) {
    ElMessage.warning('需要先有一次成功的解析')
    return
  }
  busy.value = 'extract'
  try {
    const task = await knowledgeApi.triggerExtraction({ parse_task_id: latestParse.value.id })
    ElMessage.success(`已提交抽取任务 ${task.id.slice(0, 8)}`)
    await loadTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    busy.value = ''
  }
}

async function retryParse(task: ParseTask): Promise<void> {
  try {
    await parseApi.retry(task.id)
    ElMessage.success('已重新提交')
    await loadTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function retryExtraction(task: ExtractionTask): Promise<void> {
  try {
    await knowledgeApi.retryExtraction(task.id)
    ElMessage.success('已重新提交')
    await loadTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function download(): Promise<void> {
  if (!currentVersion.value) return
  try {
    const data = await documentApi.download(documentId, currentVersion.value.id)
    window.open(data.url, '_blank', 'noopener')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function saveEdit(): Promise<void> {
  busy.value = 'edit'
  try {
    const updated = await documentApi.update(documentId, {
      title: editForm.title.trim(),
      summary: editForm.summary.trim() || null,
      sensitivity: editForm.sensitivity,
      tags: editForm.tags
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean),
    })
    doc.value = updated
    editVisible.value = false
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    busy.value = ''
  }
}

function onVersionPicked(event: Event): void {
  const target = event.target as HTMLInputElement
  versionFile.value = target.files?.[0] ?? null
}

async function uploadVersion(): Promise<void> {
  if (!versionFile.value) {
    ElMessage.warning('请选择新版本文件')
    return
  }
  busy.value = 'version'
  try {
    const form = new FormData()
    form.append('file', versionFile.value)
    if (versionNote.value.trim()) form.append('change_note', versionNote.value.trim())
    await documentApi.uploadVersion(documentId, form)
    ElMessage.success('新版本已上传')
    versionVisible.value = false
    versionFile.value = null
    versionNote.value = ''
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    busy.value = ''
  }
}

const reviewVisible = ref(false)
const reviewFlows = ref<ReviewFlow[]>([])
const reviewFlowId = ref('')
const reviewComment = ref('')
const submittingReview = ref(false)

async function openReviewSubmit(): Promise<void> {
  if (!currentVersion.value) return
  reviewVisible.value = true
  reviewComment.value = ''
  reviewFlowId.value = ''
  try {
    const data = await reviewApi.flows({ doc_type: doc.value?.doc_type })
    reviewFlows.value = data.items.filter((item) => item.status === 'active')
    const preferred = reviewFlows.value.find((item) => item.is_default) ?? reviewFlows.value[0]
    reviewFlowId.value = preferred?.id ?? ''
    if (!preferred) ElMessage.warning('该文档类型还没有可用审批流')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function submitReview(): Promise<void> {
  if (!currentVersion.value) return
  if (!reviewFlowId.value) {
    ElMessage.warning('请选择审批流')
    return
  }
  submittingReview.value = true
  try {
    await reviewApi.submit({
      document_id: documentId,
      document_version_id: currentVersion.value.id,
      flow_id: reviewFlowId.value,
      comment: reviewComment.value.trim() || null,
    })
    ElMessage.success('已提交审批，审批中版本已冻结')
    reviewVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    submittingReview.value = false
  }
}

async function removeDocument(): Promise<void> {
  try {
    await ElMessageBox.confirm('删除后可在文档库“含已删除”中恢复，确认删除？', '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await documentApi.remove(documentId)
    ElMessage.success('已删除')
    await router.push('/documents')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    if (running.value) void loadTasks()
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div v-loading="loading" class="page">
    <div class="page-head">
      <div>
        <div class="row">
          <el-button link @click="router.push('/documents')">
            <el-icon><ArrowLeft /></el-icon>
            <span>文档库</span>
          </el-button>
        </div>
        <h1 class="page-title">{{ doc?.title ?? '加载中…' }}</h1>
        <div class="row" style="margin-top: 8px">
          <el-tag v-if="doc" :type="statusMeta(DOCUMENT_STATUS, doc.status).tone" size="small">
            {{ statusMeta(DOCUMENT_STATUS, doc.status).label }}
          </el-tag>
          <el-tag v-if="doc" :type="statusMeta(SENSITIVITY, doc.sensitivity).tone" size="small" effect="plain">
            {{ statusMeta(SENSITIVITY, doc.sensitivity).label }}
          </el-tag>
          <el-tag v-for="tag in doc?.tags ?? []" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
          <span v-if="doc?.summary" class="muted small">{{ doc.summary }}</span>
        </div>
      </div>

      <div class="toolbar">
        <el-button :disabled="!currentVersion" @click="download">下载</el-button>
        <el-button v-if="auth.has('document:write')" @click="editVisible = true">编辑</el-button>
        <el-button
          v-if="auth.has('document:write')"
          plain
          :disabled="doc?.status === 'pending'"
          :loading="busy === 'version'"
          @click="versionVisible = true"
        >
          上传新版本
        </el-button>
        <el-button
          v-if="
            auth.has('review:submit') &&
            currentVersion &&
            (doc?.status === 'draft' || doc?.status === 'rejected')
          "
          type="success"
          plain
          @click="openReviewSubmit"
        >
          提交审批
        </el-button>
        <el-button
          v-if="doc?.status === 'pending' && (auth.isAdmin || auth.has('review:approve'))"
          type="warning"
          plain
          @click="router.push(`/reviews?document_id=${documentId}`)"
        >
          查看审批
        </el-button>
        <el-button
          v-if="auth.has('parse:run')"
          type="primary"
          plain
          :loading="busy === 'parse'"
          :disabled="!currentVersion"
          @click="triggerParse"
        >
          触发解析
        </el-button>
        <el-button
          v-if="auth.has('parse:run')"
          type="primary"
          :loading="busy === 'extract'"
          :disabled="!latestParse"
          @click="triggerExtraction"
        >
          触发抽取
        </el-button>
        <el-button v-if="auth.has('document:delete')" type="danger" text @click="removeDocument">删除</el-button>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="activeTab" class="detail-tabs" @tab-change="onTabChange">
        <el-tab-pane label="概览" name="overview">
          <div class="card-pad">
            <el-descriptions :column="3" border>
              <el-descriptions-item label="文档 ID">
                <span class="mono">{{ doc?.id }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="文档类型">
                {{ DOC_TYPE[doc?.doc_type ?? ''] ?? doc?.doc_type ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="来源">{{ doc?.source_type ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="当前版本">
                v{{ currentVersion?.version_no ?? '—' }} · {{ currentVersion?.file_name ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="文件大小">
                {{ formatFileSize(currentVersion?.file_size) }}
              </el-descriptions-item>
              <el-descriptions-item label="页数">{{ currentVersion?.page_count ?? '—' }}</el-descriptions-item>
              <el-descriptions-item label="创建时间">{{ formatTime(doc?.created_at) }}</el-descriptions-item>
              <el-descriptions-item label="更新时间">{{ formatTime(doc?.updated_at) }}</el-descriptions-item>
              <el-descriptions-item label="校验和">
                <span class="mono">{{ currentVersion?.checksum?.slice(0, 16) ?? '—' }}</span>
              </el-descriptions-item>
            </el-descriptions>

            <div class="grid-cards" style="margin-top: 18px">
              <div class="stat-card">
                <div class="label">版本数</div>
                <div class="value">{{ versions.length }}</div>
              </div>
              <div class="stat-card">
                <div class="label">解析任务</div>
                <div class="value">{{ parseTasks.length }}</div>
              </div>
              <div class="stat-card">
                <div class="label">抽取任务</div>
                <div class="value">{{ extractions.length }}</div>
              </div>
              <div class="stat-card">
                <div class="label">关联实体</div>
                <div class="value">{{ entities.length }}</div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="版本历史" name="versions">
          <el-table :data="versions" size="small">
            <el-table-column label="版本" width="80">
              <template #default="{ row }: { row: DocumentVersion }">v{{ row.version_no }}</template>
            </el-table-column>
            <el-table-column prop="file_name" label="文件名" min-width="200" />
            <el-table-column label="大小" width="100">
              <template #default="{ row }: { row: DocumentVersion }">
                {{ formatFileSize(row.file_size) }}
              </template>
            </el-table-column>
            <el-table-column label="页数" width="80">
              <template #default="{ row }: { row: DocumentVersion }">{{ row.page_count ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="当前" width="80">
              <template #default="{ row }: { row: DocumentVersion }">
                <el-tag v-if="row.is_current" size="small" type="success">当前</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="change_note" label="变更说明" min-width="160">
              <template #default="{ row }: { row: DocumentVersion }">
                <span class="muted small">{{ row.change_note ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="150">
              <template #default="{ row }: { row: DocumentVersion }">
                <span class="muted small">{{ formatTime(row.created_at) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="解析任务" name="parse">
          <el-table :data="parseTasks" size="small">
            <el-table-column label="任务" width="110">
              <template #default="{ row }: { row: ParseTask }">
                <span class="mono">{{ row.id.slice(0, 8) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="96">
              <template #default="{ row }: { row: ParseTask }">
                <el-tag :type="statusMeta(TASK_STATUS, row.status).tone" size="small">
                  {{ statusMeta(TASK_STATUS, row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="块数" width="80">
              <template #default="{ row }: { row: ParseTask }">{{ row.block_count }}</template>
            </el-table-column>
            <el-table-column label="质量分" width="90">
              <template #default="{ row }: { row: ParseTask }">
                <span :class="{ warn: row.low_quality }">
                  {{ row.quality_score === null ? '—' : row.quality_score.toFixed(1) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="耗时" width="110">
              <template #default="{ row }: { row: ParseTask }">
                <span class="muted small">
                  {{ row.started_at ? formatTime(row.started_at) : '未开始' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="180">
              <template #default="{ row }: { row: ParseTask }">
                <span v-if="row.error_message" class="danger-text small">{{ row.error_message }}</span>
                <span v-else class="muted small">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" align="right">
              <template #default="{ row }: { row: ParseTask }">
                <el-button link type="primary" @click="loadBlocks(row.id); activeTab = 'blocks'">
                  内容块
                </el-button>
                <el-button v-if="row.status === 'failed'" link type="warning" @click="retryParse(row)">
                  重试
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="抽取任务" name="extraction">
          <el-table :data="extractions" size="small">
            <el-table-column label="任务" width="110">
              <template #default="{ row }: { row: ExtractionTask }">
                <span class="mono">{{ row.id.slice(0, 8) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="96">
              <template #default="{ row }: { row: ExtractionTask }">
                <el-tag :type="statusMeta(TASK_STATUS, row.status).tone" size="small">
                  {{ statusMeta(TASK_STATUS, row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="本体版本" width="90">
              <template #default="{ row }: { row: ExtractionTask }">v{{ row.ontology_version }}</template>
            </el-table-column>
            <el-table-column label="候选" width="140">
              <template #default="{ row }: { row: ExtractionTask }">
                <span class="muted small">
                  实体 {{ (row.stats as any).entities ?? 0 }} · 关系 {{ (row.stats as any).relations ?? 0 }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="开始时间" width="150">
              <template #default="{ row }: { row: ExtractionTask }">
                <span class="muted small">{{ formatTime(row.started_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="错误" min-width="180">
              <template #default="{ row }: { row: ExtractionTask }">
                <span v-if="row.error_message" class="danger-text small">{{ row.error_message }}</span>
                <span v-else class="muted small">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="right">
              <template #default="{ row }: { row: ExtractionTask }">
                <el-button v-if="row.status === 'failed'" link type="warning" @click="retryExtraction(row)">
                  重试
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="关联实体" name="entities">
          <el-table :data="entities" size="small">
            <el-table-column label="实体" min-width="180">
              <template #default="{ row }: { row: EntityItem }">
                <div class="row">
                  <el-button link type="primary" @click="router.push(`/graph?entity=${row.id}`)">
                    {{ row.name }}
                  </el-button>
                  <el-tag v-if="row.aliases.length" size="small" effect="plain">
                    别名 {{ row.aliases.join('、') }}
                  </el-tag>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="100">
              <template #default="{ row }: { row: EntityItem }">{{ classLabel(row.class_code) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }: { row: EntityItem }">
                <el-tag :type="statusMeta(ENTITY_STATUS, row.status).tone" size="small">
                  {{ statusMeta(ENTITY_STATUS, row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="置信度" width="100">
              <template #default="{ row }: { row: EntityItem }">
                {{ Math.round(row.confidence * 100) }}%
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" align="right">
              <template #default="{ row }: { row: EntityItem }">
                <el-button link type="primary" @click="router.push(`/review?keyword=${row.name}`)">
                  去审核
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="还没有抽取到实体" :image-size="70" />
            </template>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="内容块" name="blocks">
          <div class="card-pad">
            <div class="toolbar" style="margin-bottom: 12px">
              <span class="muted small">
                来源任务：<span class="mono">{{ blocksTaskId.slice(0, 8) || '—' }}</span>
              </span>
              <div class="spacer" />
              <el-button v-if="latestParse" size="small" text @click="loadBlocks(latestParse.id)">
                刷新最新成功任务
              </el-button>
            </div>

            <div v-if="!blocks.length" class="muted small">暂无内容块，选择一个成功任务的“内容块”查看。</div>
            <div v-else class="block-list">
              <div v-for="block in blocks" :key="block.id" class="block">
                <div class="block-side">
                  <span class="seq">#{{ block.seq }}</span>
                  <el-tag size="small" effect="plain">{{ block.block_type }}</el-tag>
                  <span v-if="block.page_no" class="muted small">p{{ block.page_no }}</span>
                </div>
                <div class="block-content">{{ block.content }}</div>
              </div>
            </div>

            <div v-if="blocksQuery.total > blocksQuery.page_size" class="blocks-pager">
              <el-pagination
                :current-page="blocksQuery.page"
                :page-size="blocksQuery.page_size"
                :total="blocksQuery.total"
                layout="total, prev, pager, next"
                background
                @current-change="onBlocksPageChange"
              />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="editVisible" title="编辑文档" width="520px">
      <el-form label-position="top">
        <el-form-item label="标题">
          <el-input v-model="editForm.title" maxlength="200" show-word-limit />
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="editForm.summary" type="textarea" :rows="3" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label="密级">
          <el-select v-model="editForm.sensitivity" style="width: 100%">
            <el-option
              v-for="(meta, value) in SENSITIVITY"
              :key="value"
              :label="meta.label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="editForm.tags" placeholder="逗号分隔" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy === 'edit'" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="versionVisible" title="上传新版本" width="480px">
      <el-form label-position="top">
        <el-form-item label="文件">
          <input type="file" @change="onVersionPicked" />
        </el-form-item>
        <el-form-item label="变更说明">
          <el-input v-model="versionNote" type="textarea" :rows="2" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="versionVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy === 'version'" @click="uploadVersion">上传</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="reviewVisible" title="提交审批" width="520px">
      <el-form label-position="top">
        <el-form-item label="待审版本">
          <el-tag effect="plain">v{{ currentVersion?.version_no }} · {{ currentVersion?.file_name }}</el-tag>
        </el-form-item>
        <el-form-item label="审批流">
          <el-select v-model="reviewFlowId" placeholder="请选择审批流" style="width: 100%">
            <el-option
              v-for="flow in reviewFlows"
              :key="flow.id"
              :label="`${flow.name}${flow.is_default ? '（默认）' : ''}`"
              :value="flow.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="提交说明（可选）">
          <el-input v-model="reviewComment" type="textarea" :rows="2" maxlength="1000" show-word-limit />
        </el-form-item>
        <el-alert
          type="info"
          :closable="false"
          title="提交后该版本冻结：审批结束前不能上传新版本、删除文档或改状态。"
        />
      </el-form>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="submittingReview" @click="submitReview">提交审批</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.detail-tabs {
  padding: 4px 20px 0;
}

.detail-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}

.danger-text {
  color: #d92d20;
}

.warn {
  color: #b54708;
  font-weight: 600;
}

.block-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
}

.block {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fcfcfd;
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

.blocks-pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
