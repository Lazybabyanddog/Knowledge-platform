<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { departmentApi } from '@/api/departments'
import { errorMessage } from '@/api/http'
import type { DepartmentItem } from '@/api/types'
import { flattenDepartments } from '@/utils/departments'

interface DeptNode extends DepartmentItem {
  children?: DeptNode[]
}

const loading = ref(false)
const items = ref<DepartmentItem[]>([])

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<DepartmentItem | null>(null)
const form = reactive({ name: '', parent_id: '', sort_order: 0 })

const tree = computed<DeptNode[]>(() => {
  const nodes = new Map<string, DeptNode>()
  for (const item of items.value) nodes.set(item.id, { ...item })
  const roots: DeptNode[] = []
  for (const item of items.value) {
    const node = nodes.get(item.id)!
    const parent = item.parent_id ? nodes.get(item.parent_id) : undefined
    if (parent) {
      parent.children = parent.children ?? []
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  const sort = (list: DeptNode[]): void => {
    list.sort((a, b) =>
      a.sort_order !== b.sort_order
        ? a.sort_order - b.sort_order
        : a.name.localeCompare(b.name, 'zh-CN'),
    )
    for (const node of list) if (node.children?.length) sort(node.children)
  }
  sort(roots)
  return roots
})

/** 编辑时禁止把部门挂到自己或自己的下级下面 */
const blockedIds = computed<Set<string>>(() => {
  const ids = new Set<string>()
  if (!editing.value) return ids
  ids.add(editing.value.id)
  let changed = true
  while (changed) {
    changed = false
    for (const item of items.value) {
      if (item.parent_id && ids.has(item.parent_id) && !ids.has(item.id)) {
        ids.add(item.id)
        changed = true
      }
    }
  }
  return ids
})

const parentOptions = computed(() =>
  flattenDepartments(items.value).filter((item) => !blockedIds.value.has(item.value)),
)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await departmentApi.list()
    items.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate(parent?: DepartmentItem): void {
  editing.value = null
  form.name = ''
  form.parent_id = parent?.id ?? ''
  form.sort_order = 0
  dialogVisible.value = true
}

function openEdit(row: DepartmentItem): void {
  editing.value = row
  form.name = row.name
  form.parent_id = row.parent_id ?? ''
  form.sort_order = row.sort_order
  dialogVisible.value = true
}

async function save(): Promise<void> {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请填写部门名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name,
      parent_id: form.parent_id || null,
      sort_order: form.sort_order,
    }
    if (editing.value) {
      await departmentApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await departmentApi.create(payload)
      ElMessage.success('部门已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function remove(row: DepartmentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认删除「${row.name}」？存在子部门或人员时无法删除。`,
      '删除部门',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await departmentApi.remove(row.id)
    ElMessage.success('已删除')
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
        <h1 class="page-title">部门管理</h1>
        <p class="page-sub">
          最多 5 层，用于人员归属与文档按部门筛选；删除前需先清空子部门与人员。
        </p>
      </div>
      <el-button type="primary" @click="openCreate()">新建部门</el-button>
    </div>

    <div class="card">
      <div class="card-pad toolbar">
        <span class="muted small">共 {{ items.length }} 个部门</span>
        <div class="spacer" />
        <el-button text @click="load">刷新</el-button>
      </div>

      <el-table
        v-loading="loading"
        :data="tree"
        row-key="id"
        default-expand-all
        :tree-props="{ children: 'children' }"
      >
        <el-table-column label="部门名称" min-width="260">
          <template #default="{ row }: { row: DeptNode }">
            <span class="dept-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="人员数" width="110">
          <template #default="{ row }: { row: DeptNode }">
            <el-tag size="small" effect="plain" :type="row.user_count ? 'primary' : 'info'">
              {{ row.user_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="排序" width="90">
          <template #default="{ row }: { row: DeptNode }">
            <span class="muted small">{{ row.sort_order }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" align="right">
          <template #default="{ row }: { row: DeptNode }">
            <el-button link type="primary" @click="openCreate(row)">新增下级</el-button>
            <el-button link @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有部门，先建一个根部门" :image-size="70" />
        </template>
      </el-table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑部门' : '新建部门'"
      width="460px"
      destroy-on-close
    >
      <el-form label-position="top">
        <el-form-item label="部门名称">
          <el-input v-model="form.name" maxlength="64" placeholder="如 技术中心" />
        </el-form-item>
        <el-form-item label="上级部门">
          <el-select
            v-model="form.parent_id"
            placeholder="不选则为顶级部门"
            clearable
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="item in parentOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="排序（越小越靠前）">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" />
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
.dept-name {
  font-weight: 600;
  color: var(--ink);
}
</style>
