<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { reviewApi } from '@/api/reviews'
import type { ReviewInstance, ReviewInstanceDetail, ReviewStep } from '@/api/types'
import { ROLE_LABELS } from '@/constants'
import { useAuthStore } from '@/stores/auth'
import { DOC_TYPE, REVIEW_ACTION, REVIEW_STATUS, formatTime, statusMeta } from '@/utils/format'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

type TabKey = 'mine' | 'all'
type ActionMode = 'approve' | 'reject'

const tab = ref<TabKey>(route.query.document_id && auth.isAdmin ? 'all' : 'mine')
const filters = reactive({ status: 'pending', doc_type: '', page: 1, page_size: 10 })
const documentId = ref(String(route.query.document_id ?? ''))

const loading = ref(false)
const rows = ref<ReviewInstance[]>([])
const total = ref(0)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ReviewInstanceDetail | null>(null)

const actionDialog = ref(false)
const actionMode = ref<ActionMode>('approve')
const actionComment = ref('')
const actionSaving = ref(false)
const actionTarget = ref<ReviewInstance | null>(null)

const isAdmin = computed(() => auth.isAdmin)
const overdueCount = computed(() => rows.value.filter((item) => item.overdue).length)
const statusOptions = Object.entries(REVIEW_STATUS).map(([value, meta]) => ({
  value,
  label: meta.label,
}))
const docTypeOptions = Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))

async function loadReviews(): Promise<void> {
  loading.value = true
  try {
    const data = await reviewApi.list({
      scope: tab.value === 'all' ? 'all' : 'mine',
      status: filters.status || undefined,
      doc_type: filters.doc_type || undefined,
      document_id: documentId.value || undefined,
      page: filters.page,
      page_size: filters.page_size,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function search(): Promise<void> {
  filters.page = 1
  await loadReviews()
}

async function resetFilters(): Promise<void> {
  filters.status = 'pending'
  filters.doc_type = ''
  filters.page = 1
  if (documentId.value) {
    documentId.value = ''
    await router.replace({ name: 'reviews' })
  }
  await loadReviews()
}

async function clearDocumentFilter(): Promise<void> {
  documentId.value = ''
  filters.page = 1
  await router.replace({ name: 'reviews' })
  await loadReviews()
}

async function onTabChange(): Promise<void> {
  filters.page = 1
  documentId.value = String(route.query.document_id ?? '')
  await loadReviews()
}

async function openDetail(row: ReviewInstance): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await reviewApi.detail(row.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

const detailCurrentStep = computed<ReviewStep | null>(() => {
  const instance = detail.value
  if (!instance) return null
  return instance.steps[instance.current_step - 1] ?? null
})

const detailCanApprove = computed(() => {
  const instance = detail.value
  const step = detailCurrentStep.value
  if (!instance || instance.status !== 'pending' || !step) return false
  if (step.approver_type === 'role') return auth.roles.includes(step.approver_value)
  return auth.user?.id === step.approver_value
})

const detailCanWithdraw = computed(
  () => detail.value?.status === 'pending' && detail.value.submitted_by === auth.user?.id,
)

const detailStepActive = computed(() => {
  const instance = detail.value
  if (!instance) return 0
  if (instance.status === 'approved') return instance.steps.length
  return Math.max(instance.current_step - 1, 0)
})

const detailProcessStatus = computed<'wait' | 'process' | 'finish' | 'error' | 'success'>(() => {
  const status = detail.value?.status
  if (status === 'rejected') return 'error'
  if (status === 'approved') return 'success'
  if (status === 'withdrawn') return 'wait'
  return 'process'
})

function rowCanWithdraw(row: ReviewInstance): boolean {
  return row.status === 'pending' && row.submitted_by === auth.user?.id
}

function openAction(target: ReviewInstance, mode: ActionMode): void {
  actionTarget.value = target
  actionMode.value = mode
  actionComment.value = ''
  actionDialog.value = true
}

async function submitAction(): Promise<void> {
  const target = actionTarget.value
  if (!target) return
  const comment = actionComment.value.trim()
  if (actionMode.value === 'reject' && !comment) {
    ElMessage.warning('驳回必须填写理由')
    return
  }
  actionSaving.value = true
  try {
    if (actionMode.value === 'approve') {
      await reviewApi.approve(target.id, comment)
      ElMessage.success('已通过当前步骤')
    } else {
      await reviewApi.reject(target.id, comment)
      ElMessage.success('已驳回，文档退回修改')
    }
    actionDialog.value = false
    detailVisible.value = false
    await loadReviews()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    actionSaving.value = false
  }
}

async function withdraw(target: ReviewInstance): Promise<void> {
  try {
    await ElMessageBox.confirm('撤回后文档回到草稿，需重新提交审批，确认撤回？', '撤回审批', {
      type: 'warning',
      confirmButtonText: '撤回',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await reviewApi.withdraw(target.id)
    ElMessage.success('已撤回')
    detailVisible.value = false
    await loadReviews()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function recordType(action: string): 'success' | 'danger' | 'info' {
  if (action === 'approve') return 'success'
  if (action === 'reject') return 'danger'
  return 'info'
}

/* ------------------------------- 审批人展示 ------------------------------- */

function approverLabel(step: ReviewStep): string {
  if (step.approver_type === 'role') {
    return ROLE_LABELS[step.approver_value] ?? step.approver_value
  }
  return `${step.approver_value.slice(0, 8)}…`
}

onMounted(() => {
  void loadReviews()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">审批工作台</h1>
        <p class="page-sub">
          提审后冻结版本并逐级流转，最后一步通过才发布；驳回或撤回会解冻文档，超 3 个工作日标红。
        </p>
      </div>
      <div class="toolbar">
        <el-button @click="loadReviews">刷新</el-button>
        <el-button v-if="isAdmin" type="primary" plain @click="router.push('/review-flows')">
          审批流配置
        </el-button>
      </div>
    </div>

    <el-radio-group v-model="tab" class="mode-switch" @change="onTabChange">
      <el-radio-button value="mine">待我审批</el-radio-button>
      <el-radio-button v-if="isAdmin" value="all">全部审批</el-radio-button>
    </el-radio-group>

    <div class="card card-pad">
      <div class="filter-bar">
        <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 130px">
          <el-option
            v-for="item in statusOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="filters.doc_type" placeholder="全部类型" clearable style="width: 120px">
          <el-option
            v-for="item in docTypeOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-tag v-if="documentId" closable @close="clearDocumentFilter">
          仅看文档 {{ documentId.slice(0, 8) }}
        </el-tag>
        <div class="spacer" />
        <el-button type="primary" plain @click="search">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>

      <el-alert
        v-if="overdueCount"
        type="warning"
        show-icon
        :closable="false"
        :title="`当前页有 ${overdueCount} 条审批已超过 3 个工作日`"
        style="margin-bottom: 12px"
      />

      <el-table v-loading="loading" :data="rows">
        <el-table-column label="文档" min-width="230">
          <template #default="{ row }: { row: ReviewInstance }">
            <el-button link type="primary" @click="router.push(`/documents/${row.document_id}`)">
              {{ row.document_title }}
            </el-button>
            <div class="muted small">v{{ row.version_no }} · {{ row.flow_name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="当前步骤" min-width="165">
          <template #default="{ row }: { row: ReviewInstance }">
            <span v-if="row.status === 'pending'">
              {{ row.current_step }}/{{ row.total_steps }} · {{ row.current_step_name ?? '—' }}
            </span>
            <span v-else class="muted">{{ row.total_steps }} 步</span>
            <el-tag v-if="row.overdue" type="danger" size="small" style="margin-left: 6px">超时</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }: { row: ReviewInstance }">
            <el-tag :type="statusMeta(REVIEW_STATUS, row.status).tone" size="small">
              {{ statusMeta(REVIEW_STATUS, row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="提交人" width="106">
          <template #default="{ row }: { row: ReviewInstance }">
            <span class="muted small">{{ row.submitter_name ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="提交时间" width="146">
          <template #default="{ row }: { row: ReviewInstance }">
            <span class="muted small">{{ formatTime(row.submitted_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="等待" width="104">
          <template #default="{ row }: { row: ReviewInstance }">
            <span v-if="row.status === 'pending'" class="muted small">
              {{ row.waiting_workdays }} 个工作日
            </span>
            <span v-else class="muted small">{{ formatTime(row.finished_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="right">
          <template #default="{ row }: { row: ReviewInstance }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <template v-if="tab === 'mine' && row.status === 'pending'">
              <el-button link type="success" @click="openAction(row, 'approve')">通过</el-button>
              <el-button link type="danger" @click="openAction(row, 'reject')">驳回</el-button>
            </template>
            <el-button v-else-if="rowCanWithdraw(row)" link type="warning" @click="withdraw(row)">
              撤回
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="tab === 'mine' ? '没有待你处理的审批' : '没有符合条件的审批'"
            :image-size="70"
          />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="filters.page"
          v-model:page-size="filters.page_size"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="loadReviews"
        />
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="审批详情" size="560px">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="row" style="margin-bottom: 10px">
            <strong>{{ detail.document_title }}</strong>
            <el-tag :type="statusMeta(REVIEW_STATUS, detail.status).tone" size="small">
              {{ statusMeta(REVIEW_STATUS, detail.status).label }}
            </el-tag>
            <el-tag v-if="detail.overdue" type="danger" size="small">超时</el-tag>
          </div>

          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="版本">v{{ detail.version_no }}</el-descriptions-item>
            <el-descriptions-item label="审批流">{{ detail.flow_name }}</el-descriptions-item>
            <el-descriptions-item label="提交人">{{ detail.submitter_name ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="提交时间">{{ formatTime(detail.submitted_at) }}</el-descriptions-item>
            <el-descriptions-item label="当前步骤">
              {{ detail.status === 'pending' ? `${detail.current_step}/${detail.total_steps}` : '已结束' }}
            </el-descriptions-item>
            <el-descriptions-item label="等待">
              {{ detail.status === 'pending' ? `${detail.waiting_workdays} 个工作日` : formatTime(detail.finished_at) }}
            </el-descriptions-item>
          </el-descriptions>

          <h4 class="section-title">审批链路</h4>
          <el-steps
            direction="vertical"
            :active="detailStepActive"
            :process-status="detailProcessStatus"
            finish-status="success"
          >
            <el-step
              v-for="step in detail.steps"
              :key="step.step_no"
              :title="`${step.step_no}. ${step.name}`"
              :description="`审批人：${approverLabel(step)}${step.skippable ? '（可跳过）' : ''}`"
            />
          </el-steps>

          <h4 class="section-title">流转轨迹</h4>
          <el-timeline v-if="detail.records.length">
            <el-timeline-item
              v-for="record in detail.records"
              :key="record.id"
              :timestamp="formatTime(record.created_at)"
              :type="recordType(record.action)"
            >
              <div class="row">
                <strong>{{ statusMeta(REVIEW_ACTION, record.action).label }}</strong>
                <span class="muted small">第 {{ record.step_no }} 步 · {{ record.step_name ?? '—' }}</span>
              </div>
              <div class="muted small">{{ record.actor_name ?? '系统' }}：{{ record.comment || '无意见' }}</div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无审批记录" :image-size="60" />

          <div v-if="detailCanApprove || detailCanWithdraw" class="drawer-actions">
            <el-button v-if="detailCanWithdraw" @click="withdraw(detail)">撤回</el-button>
            <template v-if="detailCanApprove">
              <el-button type="danger" plain @click="openAction(detail, 'reject')">驳回</el-button>
              <el-button type="success" @click="openAction(detail, 'approve')">通过</el-button>
            </template>
          </div>
        </template>
      </div>
    </el-drawer>

    <el-dialog
      v-model="actionDialog"
      :title="actionMode === 'approve' ? '通过审批' : '驳回审批'"
      width="480px"
    >
      <el-alert
        v-if="actionTarget"
        type="info"
        :closable="false"
        :title="`${actionTarget.document_title} · 第 ${actionTarget.current_step}/${actionTarget.total_steps} 步：${actionTarget.current_step_name ?? ''}`"
        style="margin-bottom: 12px"
      />
      <el-form label-position="top">
        <el-form-item :label="actionMode === 'approve' ? '审批意见（可选）' : '驳回理由（必填）'">
          <el-input
            v-model="actionComment"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            :placeholder="actionMode === 'approve' ? '同意发布' : '说明需要补充或修改的内容'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionDialog = false">取消</el-button>
        <el-button
          :type="actionMode === 'approve' ? 'success' : 'danger'"
          :loading="actionSaving"
          @click="submitAction"
        >
          {{ actionMode === 'approve' ? '确认通过' : '确认驳回' }}
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
.mode-switch {
  margin-bottom: 14px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: 14px;
}

.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.section-title {
  margin: 20px 0 10px;
  font-size: 14px;
}
</style>
