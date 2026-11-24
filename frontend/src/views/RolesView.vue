<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { roleApi } from '@/api/roles'
import type { PermissionGroup, RoleItem } from '@/api/types'
import { PERMISSION_GROUPS, permissionLabel } from '@/constants'
import { useRolesStore } from '@/stores/roles'

const rolesStore = useRolesStore()

const loading = ref(false)
const items = ref<RoleItem[]>([])
const catalog = ref<PermissionGroup[]>([])

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<RoleItem | null>(null)
const form = reactive({
  code: '',
  name: '',
  description: '',
  permissions: [] as string[],
})

const groups = computed<PermissionGroup[]>(() =>
  catalog.value.length ? catalog.value : PERMISSION_GROUPS,
)

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await roleApi.list()
    items.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function loadCatalog(): Promise<void> {
  try {
    const data = await roleApi.permissions()
    catalog.value = data.groups
  } catch {
    // 目录接口异常时用本地字典兜底
    catalog.value = PERMISSION_GROUPS
  }
}

function openCreate(): void {
  editing.value = null
  form.code = ''
  form.name = ''
  form.description = ''
  form.permissions = []
  dialogVisible.value = true
}

function openEdit(row: RoleItem): void {
  editing.value = row
  form.code = row.code
  form.name = row.name
  form.description = row.description ?? ''
  form.permissions = [...row.permissions]
  dialogVisible.value = true
}

function toggleGroup(group: PermissionGroup, checked: boolean): void {
  const codes = group.items.map((item) => item.code)
  if (checked) {
    form.permissions = Array.from(new Set([...form.permissions, ...codes]))
  } else {
    form.permissions = form.permissions.filter((code) => !codes.includes(code))
  }
}

function groupChecked(group: PermissionGroup): boolean {
  return group.items.every((item) => form.permissions.includes(item.code))
}

function groupIndeterminate(group: PermissionGroup): boolean {
  const hit = group.items.filter((item) => form.permissions.includes(item.code)).length
  return hit > 0 && hit < group.items.length
}

async function save(): Promise<void> {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请填写角色名称')
    return
  }
  if (!editing.value && !/^[a-z][a-z0-9_-]{1,31}$/.test(form.code.trim())) {
    ElMessage.warning('角色编码需以小写字母开头，含字母、数字、下划线或中划线，2-32 位')
    return
  }
  saving.value = true
  try {
    const payload = {
      name,
      description: form.description.trim() || null,
      permissions: form.permissions,
    }
    if (editing.value) {
      await roleApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await roleApi.create({ ...payload, code: form.code.trim() })
      ElMessage.success('角色已创建')
    }
    dialogVisible.value = false
    await Promise.all([load(), rolesStore.reload()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function remove(row: RoleItem): Promise<void> {
  if (row.is_system) {
    ElMessage.warning('内置角色不可删除')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认删除角色「${row.name}」？仍有人员或审批流引用时无法删除。`,
      '删除角色',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await roleApi.remove(row.id)
    ElMessage.success('已删除')
    await Promise.all([load(), rolesStore.reload()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(() => {
  void load()
  void loadCatalog()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">角色管理</h1>
        <p class="page-sub">
          权限由角色决定；为角色勾选权限后，拥有该角色的用户会立即生效。内置角色可调整但不能删除。
        </p>
      </div>
      <el-button type="primary" @click="openCreate">新建角色</el-button>
    </div>

    <div class="card">
      <div class="card-pad toolbar">
        <span class="muted small">共 {{ items.length }} 个角色</span>
        <div class="spacer" />
        <el-button text @click="load">刷新</el-button>
      </div>

      <el-table v-loading="loading" :data="items">
        <el-table-column label="角色" min-width="200">
          <template #default="{ row }: { row: RoleItem }">
            <div class="role-cell">
              <div class="role-name">
                {{ row.name }}
                <el-tag v-if="row.is_system" size="small" type="info" effect="plain">内置</el-tag>
              </div>
              <div class="muted small mono">{{ row.code }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="220">
          <template #default="{ row }: { row: RoleItem }">
            <span class="muted small">{{ row.description ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="权限" min-width="300">
          <template #default="{ row }: { row: RoleItem }">
            <div class="row perm-row">
              <el-tag
                v-for="code in row.permissions"
                :key="code"
                size="small"
                effect="plain"
                type="info"
              >
                {{ permissionLabel(code) }}
              </el-tag>
              <span v-if="!row.permissions.length" class="muted small">未分配权限</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="人员数" width="100">
          <template #default="{ row }: { row: RoleItem }">
            <el-tag size="small" effect="plain" :type="row.user_count ? 'primary' : 'info'">
              {{ row.user_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="right">
          <template #default="{ row }: { row: RoleItem }">
            <el-button link type="primary" @click="openEdit(row)">配置</el-button>
            <el-button v-if="!row.is_system" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有角色" :image-size="70" />
        </template>
      </el-table>
    </div>

    <el-dialog
      v-model="dialogVisible"
      :title="editing ? `配置角色 · ${editing.name}` : '新建角色'"
      width="640px"
      destroy-on-close
    >
      <el-form label-position="top">
        <div class="form-row">
          <el-form-item label="角色编码">
            <el-input
              v-model="form.code"
              :disabled="Boolean(editing)"
              placeholder="如 knowledge_admin"
              maxlength="32"
            />
          </el-form-item>
          <el-form-item label="角色名称">
            <el-input v-model="form.name" placeholder="如 知识管理员" maxlength="64" />
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="这个角色负责什么" maxlength="255" />
        </el-form-item>
        <el-form-item label="权限">
          <div class="perm-groups">
            <div v-for="group in groups" :key="group.group" class="perm-group">
              <div class="perm-group-head">
                <el-checkbox
                  :model-value="groupChecked(group)"
                  :indeterminate="groupIndeterminate(group)"
                  @change="(checked: boolean | string | number) => toggleGroup(group, Boolean(checked))"
                >
                  {{ group.group }}
                </el-checkbox>
              </div>
              <el-checkbox-group v-model="form.permissions">
                <el-checkbox v-for="item in group.items" :key="item.code" :value="item.code">
                  {{ item.name }}
                </el-checkbox>
              </el-checkbox-group>
            </div>
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
.role-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.role-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.perm-row {
  flex-wrap: wrap;
}

.perm-groups {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.perm-group {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 12px 4px;
}

.perm-group-head {
  font-weight: 600;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
</style>
