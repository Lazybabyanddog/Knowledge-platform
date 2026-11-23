<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { reviewApi } from '@/api/reviews'
import type { ReviewFlow, ReviewStep, ReviewStepInput, UserItem } from '@/api/types'
import { userApi } from '@/api/users'
import { ROLE_LABELS } from '@/constants'
import { DOC_TYPE, formatTime } from '@/utils/format'

const flows = ref<ReviewFlow[]>([])
const loading = ref(false)
const users = ref<UserItem[]>([])
const usersLoaded = ref(false)

const dialogVisible = ref(false)
const editing = ref<ReviewFlow | null>(null)
const saving = ref(false)
const form = reactive({
  name: '',
  doc_type: 'pdf',
  description: '',
  is_default: true,
  status: 'active',
  steps: [] as ReviewStepInput[],
})

const docTypeOptions = Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await reviewApi.flows({ include_disabled: true })
    flows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function ensureUsers(): Promise<void> {
  if (usersLoaded.value) return
  try {
    const page = await userApi.list({ page: 1, page_size: 100 })
    users.value = page.items
    usersLoaded.value = true
  } catch {
    users.value = []
  }
}

function approverLabel(step: ReviewStep | ReviewStepInput): string {
  if (step.approver_type === 'role') {
    return ROLE_LABELS[step.approver_value] ?? step.approver_value
  }
  const user = users.value.find((item) => item.id === step.approver_value)
  return user?.display_name ?? `${step.approver_value.slice(0, 8)}…`
}

function stepSummary(flow: ReviewFlow): string {
  return flow.steps.map((step) => `${step.step_no}. ${step.name}（${approverLabel(step)}）`).join(' → ')
}

function approverOptions(step: ReviewStepInput): Array<{ value: string; label: string }> {
  if (step.approver_type === 'role') {
    return Object.entries(ROLE_LABELS).map(([value, label]) => ({ value, label }))
  }
  return users.value.map((user) => ({ value: user.id, label: user.display_name || user.username }))
}

function onApproverTypeChange(step: ReviewStepInput): void {
  step.approver_value = approverOptions(step)[0]?.value ?? ''
}

function addStep(): void {
  if (form.steps.length >= 10) return
  form.steps.push({ name: '', approver_type: 'role', approver_value: 'reviewer', skippable: false })
}

function removeStep(index: number): void {
  if (form.steps.length <= 1) return
  form.steps.splice(index, 1)
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.doc_type = 'pdf'
  form.description = ''
  form.is_default = true
  form.status = 'active'
  form.steps = [{ name: '内容审核', approver_type: 'role', approver_value: 'reviewer', skippable: false }]
  dialogVisible.value = true
  void ensureUsers()
}

function openEdit(flow: ReviewFlow): void {
  editing.value = flow
  form.name = flow.name
  form.doc_type = flow.doc_type
  form.description = flow.description ?? ''
  form.is_default = flow.is_default
  form.status = flow.status
  form.steps = flow.steps.map((step) => ({
    name: step.name,
    approver_type: step.approver_type,
    approver_value: step.approver_value,
    skippable: step.skippable,
  }))
  dialogVisible.value = true
  void ensureUsers()
}

async function save(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写流程名称')
    return
  }
  if (form.steps.length === 0 || form.steps.some((step) => !step.name.trim() || !step.approver_value)) {
    ElMessage.warning('每个步骤都需要名称与审批人')
    return
  }
  saving.value = true
  try {
    const steps = form.steps.map((step) => ({
      name: step.name.trim(),
      approver_type: step.approver_type,
      approver_value: step.approver_value,
      skippable: step.skippable,
    }))
    if (editing.value) {
      await reviewApi.updateFlow(editing.value.id, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        steps,
        is_default: form.is_default,
        status: form.status,
      })
      ElMessage.success('审批流已更新')
    } else {
      await reviewApi.createFlow({
        name: form.name.trim(),
        doc_type: form.doc_type,
        description: form.description.trim() || null,
        steps,
        is_default: form.is_default,
      })
      ElMessage.success('审批流已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function setDefault(flow: ReviewFlow): Promise<void> {
  try {
    await reviewApi.updateFlow(flow.id, { is_default: true })
    ElMessage.success(`已设为 ${DOC_TYPE[flow.doc_type] ?? flow.doc_type} 的默认流`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function toggleStatus(flow: ReviewFlow): Promise<void> {
  const next = flow.status === 'active' ? 'disabled' : 'active'
  try {
    await reviewApi.updateFlow(flow.id, { status: next })
    ElMessage.success(next === 'active' ? '已启用' : '已停用')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(flow: ReviewFlow): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除审批流「${flow.name}」吗？` +
        (flow.is_default
          ? ` 它是 ${DOC_TYPE[flow.doc_type] ?? flow.doc_type} 的默认流，删除后需重新指定默认审批流。`
          : '') +
        ' 已提交的审批实例按提交时的快照继续流转，不受影响。',
      '删除审批流',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await reviewApi.deleteFlow(flow.id)
    ElMessage.success('审批流已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(async () => {
  await Promise.all([load(), ensureUsers()])
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">审批流配置</h1>
        <p class="page-sub">
          按文档类型配置多级审批；每种类型至多一个默认流，修改只影响新提交的实例（旧实例使用快照）。
        </p>
      </div>
      <div class="toolbar">
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建审批流</el-button>
      </div>
    </div>

    <div class="card card-pad">
      <el-table v-loading="loading" :data="flows">
        <el-table-column label="审批流" min-width="190">
          <template #default="{ row }: { row: ReviewFlow }">
            <div class="row">
              <strong>{{ row.name }}</strong>
              <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
              <el-tag v-if="row.status !== 'active'" type="info" size="small">停用</el-tag>
            </div>
            <div class="muted small">{{ row.description ?? '暂无描述' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="88">
          <template #default="{ row }: { row: ReviewFlow }">
            {{ DOC_TYPE[row.doc_type] ?? row.doc_type }}
          </template>
        </el-table-column>
        <el-table-column label="步骤" min-width="260">
          <template #default="{ row }: { row: ReviewFlow }">
            <span class="small">{{ stepSummary(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="150">
          <template #default="{ row }: { row: ReviewFlow }">
            <span class="muted small">{{ formatTime(row.updated_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="270" align="right">
          <template #default="{ row }: { row: ReviewFlow }">
            <el-button link type="primary" @click="openEdit(row)">修改</el-button>
            <el-button v-if="!row.is_default && row.status === 'active'" link @click="setDefault(row)">
              设为默认
            </el-button>
            <el-button
              link
              :type="row.status === 'active' ? 'warning' : 'success'"
              @click="toggleStatus(row)"
            >
              {{ row.status === 'active' ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有审批流，先建一条默认流" :image-size="70" />
        </template>
      </el-table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '修改审批流' : '新建审批流'"
      width="760px"
      destroy-on-close
    >
      <el-form label-position="top">
        <div class="form-row">
          <el-form-item label="流程名称">
            <el-input v-model="form.name" maxlength="128" placeholder="如 制度文件两级审批" />
          </el-form-item>
          <el-form-item label="文档类型">
            <el-select v-model="form.doc_type" :disabled="Boolean(editing)" style="width: 100%">
              <el-option
                v-for="item in docTypeOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="设为该类型默认流">
            <el-switch v-model="form.is_default" />
          </el-form-item>
          <el-form-item v-if="editing" label="状态">
            <el-select v-model="form.status" style="width: 100%">
              <el-option label="启用" value="active" />
              <el-option label="停用" value="disabled" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="审批步骤（按顺序流转）">
          <div style="width: 100%">
            <div v-for="(step, index) in form.steps" :key="index" class="step-row">
              <span class="step-no">{{ index + 1 }}</span>
              <el-input v-model="step.name" placeholder="步骤名称" maxlength="64" style="width: 160px" />
              <el-select
                v-model="step.approver_type"
                style="width: 100px"
                @change="onApproverTypeChange(step)"
              >
                <el-option label="按角色" value="role" />
                <el-option label="指定用户" value="user" />
              </el-select>
              <el-select v-model="step.approver_value" filterable style="width: 160px">
                <el-option
                  v-for="option in approverOptions(step)"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
              <el-checkbox v-model="step.skippable">可跳过</el-checkbox>
              <el-button link type="danger" :disabled="form.steps.length <= 1" @click="removeStep(index)">
                移除
              </el-button>
            </div>
            <el-button link type="primary" :disabled="form.steps.length >= 10" @click="addStep">
              + 添加步骤
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.step-no {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--brand-soft, #eef2ff);
  color: var(--brand, #4f46e5);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
</style>
