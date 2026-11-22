<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { knowledgeApi } from '@/api/knowledge'
import { ontologyApi } from '@/api/ontology'
import type { EntityItem, EntitySource, OntologyClass, RelationItem } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import {
  ENTITY_STATUS,
  RELATION_STATUS,
  classLabel,
  formatTime,
  statusMeta,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const tab = ref<'entities' | 'relations'>('entities')
const loading = ref(false)
const rows = ref<EntityItem[]>([])
const total = ref(0)
const selected = ref<EntityItem[]>([])
const classCodes = ref<OntologyClass[]>([])
const entityMap = ref<Record<string, string>>({})

const query = reactive({
  status: 'proposed',
  class_code: '',
  keyword: typeof route.query.keyword === 'string' ? route.query.keyword : '',
  page: 1,
  page_size: 10,
})

const relationLoading = ref(false)
const relations = ref<RelationItem[]>([])
const relationTotal = ref(0)
const relationQuery = reactive({ status: 'proposed', code: '', page: 1, page_size: 10 })

const sourceVisible = ref(false)
const sourceLoading = ref(false)
const source = ref<EntitySource | null>(null)
const sourceEntity = ref<EntityItem | null>(null)

const mergeVisible = ref(false)
const mergeTarget = ref('')
const merging = ref(false)
const lastMerge = ref<{ merge_id: string; target_id: string; source_ids: string[]; undo_deadline: string } | null>(
  (() => {
    const raw = localStorage.getItem('ai-platform-last-merge')
    return raw ? JSON.parse(raw) : null
  })(),
)

const canReview = computed(() => auth.has('ontology:review'))
const statusOptions = Object.entries(ENTITY_STATUS).map(([value, meta]) => ({
  value,
  label: meta.label,
}))
const relationStatusOptions = Object.entries(RELATION_STATUS).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

function entityName(id: string): string {
  return entityMap.value[id] ?? id.slice(0, 8)
}

async function loadClasses(): Promise<void> {
  try {
    const data = await ontologyApi.classes()
    classCodes.value = data.items
  } catch {
    classCodes.value = []
  }
}

async function loadEntities(): Promise<void> {
  loading.value = true
  try {
    const data = await knowledgeApi.entities({
      status: query.status || undefined,
      class_code: query.class_code || undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      page_size: query.page_size,
    })
    rows.value = data.items
    total.value = data.total
    await loadEntityMap()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadEntityMap(): Promise<void> {
  try {
    const data = await knowledgeApi.entities({ page: 1, page_size: 100 })
    const map: Record<string, string> = {}
    for (const item of data.items) map[item.id] = item.name
    entityMap.value = map
  } catch {
    // 名称映射失败不影响主流程，回退显示 id
  }
}

async function loadRelations(): Promise<void> {
  relationLoading.value = true
  try {
    const data = await knowledgeApi.relations({
      status: relationQuery.status || undefined,
      code: relationQuery.code || undefined,
      page: relationQuery.page,
      page_size: relationQuery.page_size,
    })
    relations.value = data.items
    relationTotal.value = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    relationLoading.value = false
  }
}

function search(): void {
  query.page = 1
  void loadEntities()
}

function searchRelations(): void {
  relationQuery.page = 1
  void loadRelations()
}

function onSelectionChange(items: EntityItem[]): void {
  selected.value = items
}

async function confirmEntity(row: EntityItem): Promise<void> {
  let name: string
  try {
    const result = await ElMessageBox.prompt('可同时修正名称，留空则保持原样', '确认实体', {
      inputValue: row.name,
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      inputPlaceholder: row.name,
    })
    name = result.value.trim()
  } catch {
    return
  }
  try {
    await knowledgeApi.confirmEntity(row.id, name && name !== row.name ? { name } : {})
    ElMessage.success(name && name !== row.name ? `已确认，并改名为「${name}」` : '已确认')
    await loadEntities()
    await loadRelations()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function rejectEntity(row: EntityItem): Promise<void> {
  let reason: string
  try {
    const result = await ElMessageBox.prompt('驳回理由会写入审计日志，必填', '驳回候选实体', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputPlaceholder: '例如：抽取片段不是有效实体',
      inputValidator: (value) => (value?.trim() ? true : '请填写驳回理由'),
    })
    reason = result.value.trim()
  } catch {
    return
  }
  try {
    await knowledgeApi.rejectEntity(row.id, reason)
    ElMessage.success('已驳回')
    await loadEntities()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function showSource(row: EntityItem): Promise<void> {
  sourceEntity.value = row
  sourceVisible.value = true
  sourceLoading.value = true
  source.value = null
  try {
    source.value = await knowledgeApi.entitySources(row.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    sourceLoading.value = false
  }
}

function openMerge(): void {
  const confirmed = selected.value.filter((item) => item.status !== 'merged')
  if (confirmed.length < 2) {
    ElMessage.warning('至少选择两条待合并实体')
    return
  }
  mergeTarget.value = confirmed[0].id
  mergeVisible.value = true
}

async function submitMerge(): Promise<void> {
  if (!mergeTarget.value) {
    ElMessage.warning('请选择合并后的主实体')
    return
  }
  const sourceIds = selected.value.filter((item) => item.id !== mergeTarget.value).map((item) => item.id)
  if (!sourceIds.length) {
    ElMessage.warning('除主实体外还需要至少一条被合并实体')
    return
  }
  merging.value = true
  try {
    const result = await knowledgeApi.mergeEntities({ target_id: mergeTarget.value, source_ids: sourceIds })
    lastMerge.value = result
    localStorage.setItem('ai-platform-last-merge', JSON.stringify(result))
    mergeVisible.value = false
    ElMessage.success(`已合并 ${result.merged_count} 条实体`)
    await loadEntities()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    merging.value = false
  }
}

async function undoMerge(): Promise<void> {
  if (!lastMerge.value) return
  try {
    await ElMessageBox.confirm('撤销会恢复被合并实体的状态与关系指向，确认撤销？', '撤销合并', {
      type: 'warning',
      confirmButtonText: '撤销',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await knowledgeApi.undoMerge({
      target_id: lastMerge.value.target_id,
      merge_id: lastMerge.value.merge_id,
    })
    lastMerge.value = null
    localStorage.removeItem('ai-platform-last-merge')
    ElMessage.success('已撤销合并')
    await loadEntities()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function dismissMerge(): void {
  lastMerge.value = null
  localStorage.removeItem('ai-platform-last-merge')
}

async function confirmRelation(row: RelationItem): Promise<void> {
  try {
    await knowledgeApi.confirmRelation(row.id)
    ElMessage.success('关系已确认')
    await loadRelations()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function rejectRelation(row: RelationItem): Promise<void> {
  let reason: string
  try {
    const result = await ElMessageBox.prompt('驳回理由必填', '驳回关系', {
      confirmButtonText: '驳回',
      cancelButtonText: '取消',
      inputValidator: (value) => (value?.trim() ? true : '请填写驳回理由'),
    })
    reason = result.value.trim()
  } catch {
    return
  }
  try {
    await knowledgeApi.rejectRelation(row.id, reason)
    ElMessage.success('已驳回')
    await loadRelations()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(async () => {
  await Promise.all([loadClasses(), loadEntities(), loadRelations()])
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">候选知识审核</h1>
        <p class="page-sub">
          抽取结果先以"待审核"落库，确认后才进入图谱；合并可在 7 天内撤销。
        </p>
      </div>
    </div>

    <el-alert
      v-if="lastMerge"
      type="success"
      show-icon
      :closable="false"
      class="merge-banner"
    >
      <div class="row" style="width: 100%">
        <span>
          最近一次合并：{{ lastMerge.source_ids.length }} 条 → 主实体
          <span class="mono">{{ lastMerge.target_id.slice(0, 8) }}</span>，可撤销至
          {{ formatTime(lastMerge.undo_deadline) }}
        </span>
        <div class="spacer" />
        <el-button v-if="canReview" size="small" type="warning" @click="undoMerge">撤销合并</el-button>
        <el-button size="small" text @click="dismissMerge">忽略</el-button>
      </div>
    </el-alert>

    <div class="card">
      <el-tabs v-model="tab" class="review-tabs" @tab-change="tab === 'entities' ? loadEntities() : loadRelations()">
        <el-tab-pane label="实体候选" name="entities">
          <div class="card-pad toolbar">
            <el-input
              v-model="query.keyword"
              placeholder="搜索名称"
              clearable
              style="width: 200px"
              @keyup.enter="search"
              @clear="search"
            />
            <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 120px">
              <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <el-select v-model="query.class_code" placeholder="全部类型" clearable style="width: 130px">
              <el-option
                v-for="item in classCodes"
                :key="item.code"
                :label="item.name"
                :value="item.code"
              />
            </el-select>
            <el-button type="primary" plain @click="search">查询</el-button>
            <div class="spacer" />
            <span class="muted small">已选 {{ selected.length }} 条</span>
            <el-button v-if="canReview" :disabled="selected.length < 2" type="primary" @click="openMerge">
              合并所选
            </el-button>
          </div>

          <el-table
            v-loading="loading"
            :data="rows"
            row-key="id"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="46" :selectable="(row: EntityItem) => row.status !== 'merged'" />
            <el-table-column label="实体" min-width="240">
              <template #default="{ row }: { row: EntityItem }">
                <div class="entity-name">
                  {{ row.name }}
                  <el-tag v-if="row.status === 'merged'" size="small" type="info">
                    → {{ entityName(row.merged_into ?? '') }}
                  </el-tag>
                </div>
                <div v-if="row.aliases.length" class="muted small">别名：{{ row.aliases.join('、') }}</div>
                <div v-if="row.description" class="muted small">{{ row.description }}</div>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="100">
              <template #default="{ row }: { row: EntityItem }">{{ classLabel(row.class_code) }}</template>
            </el-table-column>
            <el-table-column label="置信度" width="110">
              <template #default="{ row }: { row: EntityItem }">
                <div class="conf">
                  <div class="conf-bar">
                    <span :style="{ width: `${Math.round(row.confidence * 100)}%` }" />
                  </div>
                  <span class="muted small">{{ Math.round(row.confidence * 100) }}%</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="96">
              <template #default="{ row }: { row: EntityItem }">
                <el-tag :type="statusMeta(ENTITY_STATUS, row.status).tone" size="small">
                  {{ statusMeta(ENTITY_STATUS, row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="证据" width="80">
              <template #default="{ row }: { row: EntityItem }">
                <el-button link type="primary" @click="showSource(row)">
                  {{ row.evidence_block_ids.length }} 块
                </el-button>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" align="right">
              <template #default="{ row }: { row: EntityItem }">
                <el-button link type="primary" @click="router.push(`/graph?entity=${row.id}`)">图谱</el-button>
                <template v-if="canReview && row.status === 'proposed'">
                  <el-button link type="success" @click="confirmEntity(row)">确认</el-button>
                  <el-button link type="danger" @click="rejectEntity(row)">驳回</el-button>
                </template>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="当前筛选下没有候选实体" :image-size="70" />
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
              @current-change="loadEntities"
              @size-change="search"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="关系候选" name="relations">
          <div class="card-pad toolbar">
            <el-select v-model="relationQuery.status" placeholder="全部状态" clearable style="width: 120px">
              <el-option
                v-for="item in relationStatusOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
            <el-input
              v-model="relationQuery.code"
              placeholder="关系编码，如 works_at"
              clearable
              style="width: 200px"
              @keyup.enter="searchRelations"
              @clear="searchRelations"
            />
            <el-button type="primary" plain @click="searchRelations">查询</el-button>
          </div>

          <el-table v-loading="relationLoading" :data="relations">
            <el-table-column label="起点" min-width="150">
              <template #default="{ row }: { row: RelationItem }">{{ entityName(row.source_id) }}</template>
            </el-table-column>
            <el-table-column label="关系" width="150">
              <template #default="{ row }: { row: RelationItem }">
                <el-tag size="small" effect="plain" class="mono">{{ row.code }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="终点" min-width="150">
              <template #default="{ row }: { row: RelationItem }">{{ entityName(row.target_id) }}</template>
            </el-table-column>
            <el-table-column label="置信度" width="100">
              <template #default="{ row }: { row: RelationItem }">
                {{ Math.round(row.confidence * 100) }}%
              </template>
            </el-table-column>
            <el-table-column label="状态" width="96">
              <template #default="{ row }: { row: RelationItem }">
                <el-tag :type="statusMeta(RELATION_STATUS, row.status).tone" size="small">
                  {{ statusMeta(RELATION_STATUS, row.status).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" align="right">
              <template #default="{ row }: { row: RelationItem }">
                <template v-if="canReview && row.status === 'proposed'">
                  <el-button link type="success" @click="confirmRelation(row)">确认</el-button>
                  <el-button link type="danger" @click="rejectRelation(row)">驳回</el-button>
                </template>
                <span v-else class="muted small">—</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="当前筛选下没有候选关系" :image-size="70" />
            </template>
          </el-table>

          <div class="card-pad pager">
            <el-pagination
              v-model:current-page="relationQuery.page"
              v-model:page-size="relationQuery.page_size"
              :total="relationTotal"
              :page-sizes="[10, 20, 50]"
              layout="total, sizes, prev, pager, next"
              background
              @current-change="loadRelations"
              @size-change="searchRelations"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-drawer v-model="sourceVisible" title="实体来源" size="46%">
      <div v-loading="sourceLoading" class="stack">
        <div v-if="sourceEntity" class="row">
          <strong>{{ sourceEntity.name }}</strong>
          <el-tag size="small" effect="plain">{{ classLabel(sourceEntity.class_code) }}</el-tag>
        </div>
        <div v-if="source" class="muted small">
          来源文档 {{ source.document_ids.length }} 份 · 证据块 {{ source.blocks.length }} 个
        </div>
        <el-empty v-if="!sourceLoading && !source?.blocks.length" description="没有可定位的证据块" :image-size="70" />
        <div v-for="block in source?.blocks ?? []" :key="block.block_id" class="source-block">
          <div class="row">
            <el-tag size="small" effect="plain">第 {{ block.page_no ?? '?' }} 页</el-tag>
            <span class="mono muted small">{{ block.block_id.slice(0, 8) }}</span>
            <div class="spacer" />
            <el-button
              link
              type="primary"
              @click="router.push(`/documents/${block.document_id}`)"
            >
              打开文档
            </el-button>
          </div>
          <div class="snippet">{{ block.snippet }}</div>
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="mergeVisible" title="合并实体" width="520px">
      <p class="muted small">
        所有选中实体的别名、证据与文档会并入主实体，被合并端点上的关系将改挂到主实体。
      </p>
      <el-form label-position="top">
        <el-form-item label="主实体（保留）">
          <el-select v-model="mergeTarget" style="width: 100%">
            <el-option
              v-for="item in selected.filter((row) => row.status !== 'merged')"
              :key="item.id"
              :label="`${item.name}（${classLabel(item.class_code)}）`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="被合并实体">
          <div class="merge-list">
            <el-tag
              v-for="item in selected.filter((row) => row.id !== mergeTarget)"
              :key="item.id"
              type="info"
            >
              {{ item.name }}
            </el-tag>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeVisible = false">取消</el-button>
        <el-button type="primary" :loading="merging" @click="submitMerge">确认合并</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.merge-banner {
  align-items: center;
}

.review-tabs {
  padding: 4px 20px 0;
}

.review-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}

.pager {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--line);
}

.entity-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.conf {
  display: flex;
  align-items: center;
  gap: 8px;
}

.conf-bar {
  width: 52px;
  height: 5px;
  border-radius: 4px;
  background: #eef0f4;
  overflow: hidden;
}

.conf-bar span {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: linear-gradient(90deg, #6366f1, #8b5cf6);
}

.source-block {
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 10px 12px;
  background: #fcfcfd;
}

.snippet {
  margin-top: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-soft);
  white-space: pre-wrap;
}

.merge-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
