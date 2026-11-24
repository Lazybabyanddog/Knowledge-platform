<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { errorMessage } from '@/api/http'
import { qaApi } from '@/api/qa'
import type {
  QaAssignee,
  QaDisposition,
  QaIssue,
  QaIssueDetail,
  QaIssueEvent,
  QaRecordItem,
} from '@/api/types'
import {
  QA_DISPOSITION,
  QA_ISSUE_ACTION,
  QA_ISSUE_STATUS,
  QA_REASON,
  SENSITIVITY,
  formatPercent,
  formatTime,
  statusMeta,
} from '@/utils/format'

type ActionMode = 'assign' | 'dispose' | 'close' | 'reopen'

const filters = reactive({
  status: 'pending',
  disposition: '',
  reason: '',
  assignee_id: '',
  range: null as string[] | null,
  page: 1,
  page_size: 20,
})

const loading = ref(false)
const rows = ref<QaIssue[]>([])
const total = ref(0)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<QaIssueDetail | null>(null)

const assignees = ref<QaAssignee[]>([])

const actionOpen = ref(false)
const actionMode = ref<ActionMode>('assign')
const actionSaving = ref(false)
const actionNote = ref('')
const actionAssignee = ref('')
const actionDisposition = ref<QaDisposition | ''>('')

const statusOptions = Object.entries(QA_ISSUE_STATUS).map(([value, meta]) => ({
  value,
  label: meta.label,
}))
const dispositionOptions = Object.entries(QA_DISPOSITION).map(([value, label]) => ({
  value,
  label,
}))
const reasonOptions = Object.entries(QA_REASON).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

const ACTION_TITLE: Record<ActionMode, string> = {
  assign: '指派处理人',
  dispose: '设置处置结论',
  close: '关闭问题簇',
  reopen: '重开问题簇',
}

function isoBoundary(date: string, endOfDay: boolean): string {
  return new Date(`${date}T${endOfDay ? '23:59:59' : '00:00:00'}`).toISOString()
}

async function loadIssues(): Promise<void> {
  loading.value = true
  try {
    const data = await qaApi.listIssues({
      status: filters.status || undefined,
      disposition: filters.disposition || undefined,
      reason: filters.reason || undefined,
      assignee_id: filters.assignee_id || undefined,
      created_from: filters.range?.[0] ? isoBoundary(filters.range[0], false) : undefined,
      created_to: filters.range?.[1] ? isoBoundary(filters.range[1], true) : undefined,
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
  await loadIssues()
}

async function resetFilters(): Promise<void> {
  filters.status = 'pending'
  filters.disposition = ''
  filters.reason = ''
  filters.assignee_id = ''
  filters.range = []
  filters.page = 1
  await loadIssues()
}

async function loadAssignees(): Promise<void> {
  try {
    assignees.value = await qaApi.assignees()
  } catch {
    // 指派下拉不可用时不影响列表与详情使用
    assignees.value = []
  }
}

async function openDetail(row: QaIssue): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await qaApi.issueDetail(row.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

function dispositionLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return QA_DISPOSITION[value] ?? value
}

function reasonLabel(value: string): string {
  return QA_REASON[value]?.label ?? value
}

function sensitivityMeta(value: string) {
  return statusMeta(SENSITIVITY, value)
}

function eventDetail(event: QaIssueEvent): string {
  const parts: string[] = []
  const disposition = event.detail?.disposition
  if (typeof disposition === 'string' && disposition) {
    parts.push(`结论：${dispositionLabel(disposition)}`)
  }
  const assignee = event.detail?.assignee_name
  if (typeof assignee === 'string' && assignee) {
    parts.push(`处理人：${assignee}`)
  }
  return parts.join(' · ')
}

function recordSources(record: QaRecordItem): string {
  const titles = record.sources
    .map((source) => source.title)
    .filter((title): title is string => !!title)
  if (!titles.length) return '无命中来源'
  return titles.length > 3 ? `${titles.slice(0, 3).join('、')} 等 ${titles.length} 篇` : titles.join('、')
}

/* ------------------------------- 处置动作 ------------------------------- */

function openAction(mode: ActionMode): void {
  const issue = detail.value
  if (!issue) return
  actionMode.value = mode
  actionNote.value = ''
  actionAssignee.value = issue.assignee_id ?? ''
  actionDisposition.value = (issue.disposition as QaDisposition | null) ?? ''
  actionOpen.value = true
}

async function submitAction(): Promise<void> {
  const issue = detail.value
  if (!issue) return
  if (actionMode.value === 'assign' && !actionAssignee.value) {
    ElMessage.warning('请选择处理人')
    return
  }
  if (
    (actionMode.value === 'dispose' || actionMode.value === 'close') &&
    !actionDisposition.value
  ) {
    ElMessage.warning('请给出处置结论')
    return
  }

  actionSaving.value = true
  try {
    const note = actionNote.value.trim() || null
    let data: QaIssueDetail
    if (actionMode.value === 'assign') {
      data = await qaApi.assign(issue.id, { assignee_id: actionAssignee.value || null, note })
    } else if (actionMode.value === 'dispose') {
      data = await qaApi.dispose(issue.id, {
        disposition: actionDisposition.value as QaDisposition,
        note,
      })
    } else if (actionMode.value === 'close') {
      data = await qaApi.close(issue.id, {
        disposition: actionDisposition.value as QaDisposition,
        note,
      })
    } else {
      data = await qaApi.reopen(issue.id, { note })
    }
    detail.value = data
    actionOpen.value = false
    ElMessage.success('处置动作已记录')
    await loadIssues()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    actionSaving.value = false
  }
}

onMounted(() => {
  void loadIssues()
  void loadAssignees()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">问答治理</h1>
        <p class="page-sub">
          无命中、无引用与被点踩的问答按问题簇聚合；处置结论与轨迹只追加，便于复盘与验收。
        </p>
      </div>
      <div class="toolbar">
        <el-button @click="loadIssues">刷新</el-button>
      </div>
    </div>

    <div class="card card-pad">
      <div class="filter-bar">
        <el-select v-model="filters.status" placeholder="全部状态" clearable style="width: 120px">
          <el-option
            v-for="item in statusOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select
          v-model="filters.disposition"
          placeholder="全部结论"
          clearable
          style="width: 132px"
        >
          <el-option
            v-for="item in dispositionOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="filters.reason" placeholder="全部原因" clearable style="width: 140px">
          <el-option
            v-for="item in reasonOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select
          v-model="filters.assignee_id"
          placeholder="全部处理人"
          clearable
          filterable
          style="width: 150px"
        >
          <el-option
            v-for="user in assignees"
            :key="user.id"
            :label="user.display_name"
            :value="user.id"
          />
        </el-select>
        <el-date-picker
          v-model="filters.range"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="最近发生起"
          end-placeholder="止"
          style="width: 240px"
        />
        <div class="spacer" />
        <el-button type="primary" plain @click="search">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="rows">
        <el-table-column label="问题簇" min-width="280">
          <template #default="{ row }: { row: QaIssue }">
            <div class="question">{{ row.representative_question }}</div>
            <div class="row tag-row">
              <el-tag
                v-for="reason in row.reasons"
                :key="reason"
                size="small"
                :type="statusMeta(QA_REASON, reason).tone"
                effect="light"
              >
                {{ reasonLabel(reason) }}
              </el-tag>
              <el-tag size="small" :type="sensitivityMeta(row.sensitivity).tone" effect="plain">
                {{ sensitivityMeta(row.sensitivity).label }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="影响面" width="150">
          <template #default="{ row }: { row: QaIssue }">
            <div class="small">{{ row.record_count }} 次问答</div>
            <div class="muted small">
              不认可 {{ row.dislike_count }} · 命中 {{ row.hit_count ?? 0 }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="来源文档" min-width="170">
          <template #default="{ row }: { row: QaIssue }">
            <el-button
              v-if="row.primary_document_id"
              link
              type="primary"
              @click="$router.push(`/documents/${row.primary_document_id}`)"
            >
              {{ row.primary_document_title ?? row.primary_document_id.slice(0, 8) }}
            </el-button>
            <span v-else class="muted small">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态 / 结论" width="150">
          <template #default="{ row }: { row: QaIssue }">
            <el-tag :type="statusMeta(QA_ISSUE_STATUS, row.status).tone" size="small">
              {{ statusMeta(QA_ISSUE_STATUS, row.status).label }}
            </el-tag>
            <div class="muted small">{{ dispositionLabel(row.disposition) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="处理人" width="110">
          <template #default="{ row }: { row: QaIssue }">
            <span class="muted small">{{ row.assignee_name ?? '未指派' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近发生" width="146">
          <template #default="{ row }: { row: QaIssue }">
            <span class="muted small">{{ formatTime(row.last_seen_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="86" align="right">
          <template #default="{ row }: { row: QaIssue }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有符合条件的待处置问题" :image-size="70" />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="filters.page"
          v-model:page-size="filters.page_size"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="loadIssues"
        />
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="问题簇详情" size="640px">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="row" style="margin-bottom: 10px">
            <strong>{{ detail.representative_question }}</strong>
            <el-tag :type="statusMeta(QA_ISSUE_STATUS, detail.status).tone" size="small">
              {{ statusMeta(QA_ISSUE_STATUS, detail.status).label }}
            </el-tag>
            <el-tag :type="sensitivityMeta(detail.sensitivity).tone" size="small" effect="plain">
              {{ sensitivityMeta(detail.sensitivity).label }}
            </el-tag>
          </div>

          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="影响面">
              {{ detail.record_count }} 次问答 · 不认可 {{ detail.dislike_count }}
            </el-descriptions-item>
            <el-descriptions-item label="处置结论">
              {{ dispositionLabel(detail.disposition) }}
            </el-descriptions-item>
            <el-descriptions-item label="处理人">
              {{ detail.assignee_name ?? '未指派' }}
            </el-descriptions-item>
            <el-descriptions-item label="命中片段">
              {{ detail.hit_count ?? 0 }}
            </el-descriptions-item>
            <el-descriptions-item label="首次发生">
              {{ formatTime(detail.first_seen_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="最近发生">
              {{ formatTime(detail.last_seen_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="来源文档" :span="2">
              <el-button
                v-if="detail.primary_document_id"
                link
                type="primary"
                @click="$router.push(`/documents/${detail.primary_document_id}`)"
              >
                {{ detail.primary_document_title ?? detail.primary_document_id }}
              </el-button>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="触发原因" :span="2">
              <div class="row">
                <el-tag
                  v-for="reason in detail.reasons"
                  :key="reason"
                  size="small"
                  :type="statusMeta(QA_REASON, reason).tone"
                  effect="light"
                >
                  {{ reasonLabel(reason) }}
                </el-tag>
                <span v-if="!detail.reasons.length" class="muted small">—</span>
              </div>
            </el-descriptions-item>
          </el-descriptions>

          <div class="drawer-actions">
            <el-button @click="openAction('assign')">指派</el-button>
            <el-button @click="openAction('dispose')">设置结论</el-button>
            <el-button v-if="detail.status === 'closed'" type="success" @click="openAction('reopen')">
              重开
            </el-button>
            <el-button v-else type="danger" plain @click="openAction('close')">关闭</el-button>
          </div>

          <h4 class="section-title">成员问答（{{ detail.records.length }}）</h4>
          <div v-if="detail.records.length" class="record-list">
            <article v-for="record in detail.records" :key="record.id" class="record-item">
              <div class="row">
                <strong class="small">{{ record.question }}</strong>
                <el-tag
                  v-for="reason in record.reasons"
                  :key="reason"
                  size="small"
                  :type="statusMeta(QA_REASON, reason).tone"
                  effect="light"
                >
                  {{ reasonLabel(reason) }}
                </el-tag>
              </div>
              <p class="record-answer">{{ record.answer_excerpt || '（无回答正文）' }}</p>
              <div class="muted small">
                {{ record.user_name ?? '未知用户' }} · {{ formatTime(record.created_at) }} ·
                命中 {{ record.hit_count }} · 最高分 {{ formatPercent(record.top_score) }}
              </div>
              <div class="muted small">来源：{{ recordSources(record) }}</div>
            </article>
          </div>
          <el-empty v-else description="暂无可见的成员问答" :image-size="60" />

          <h4 class="section-title">处置轨迹</h4>
          <el-timeline v-if="detail.events.length">
            <el-timeline-item
              v-for="event in detail.events"
              :key="event.id"
              :timestamp="formatTime(event.created_at)"
              :type="statusMeta(QA_ISSUE_ACTION, event.action).tone"
            >
              <div class="row">
                <strong>{{ statusMeta(QA_ISSUE_ACTION, event.action).label }}</strong>
                <span class="muted small">{{ event.actor_name ?? '系统' }}</span>
              </div>
              <div v-if="eventDetail(event)" class="muted small">{{ eventDetail(event) }}</div>
              <div class="muted small">{{ event.note || '无备注' }}</div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无处置记录" :image-size="60" />
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="actionOpen" :title="ACTION_TITLE[actionMode]" width="480px">
      <el-alert
        v-if="detail"
        type="info"
        :closable="false"
        :title="`${detail.representative_question} · ${detail.record_count} 次问答`"
        style="margin-bottom: 12px"
      />
      <el-form label-position="top">
        <el-form-item v-if="actionMode === 'assign'" label="处理人">
          <el-select v-model="actionAssignee" placeholder="选择本空间用户" filterable style="width: 100%">
            <el-option
              v-for="user in assignees"
              :key="user.id"
              :label="`${user.display_name}（${user.username}）`"
              :value="user.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="actionMode === 'dispose' || actionMode === 'close'"
          label="处置结论（必选）"
        >
          <el-select v-model="actionDisposition" placeholder="选择结论" style="width: 100%">
            <el-option
              v-for="item in dispositionOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="actionMode === 'reopen' ? '重开说明' : '备注（可选）'">
          <el-input
            v-model="actionNote"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            :placeholder="actionMode === 'reopen' ? '例如：文档补完后再复核' : '补充处置依据'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionOpen = false">取消</el-button>
        <el-button type="primary" :loading="actionSaving" @click="submitAction">
          确认{{ ACTION_TITLE[actionMode] }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
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

.question {
  font-weight: 600;
  font-size: 13.5px;
}

.tag-row {
  margin-top: 6px;
  gap: 6px;
}

.record-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.record-item {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fafbfc;
}

.record-answer {
  margin: 8px 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}
</style>
