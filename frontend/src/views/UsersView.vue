<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { departmentApi } from '@/api/departments'
import { errorMessage } from '@/api/http'
import { userApi } from '@/api/users'
import type { DepartmentItem, UserItem } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useRolesStore } from '@/stores/roles'
import { flattenDepartments } from '@/utils/departments'
import { formatTime } from '@/utils/format'

const auth = useAuthStore()
const roles = useRolesStore()

const loading = ref(false)
const rows = ref<UserItem[]>([])
const total = ref(0)
const departments = ref<DepartmentItem[]>([])
const query = reactive({
  keyword: '',
  status: '',
  department_id: '',
  page: 1,
  page_size: 10,
})

const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<UserItem | null>(null)
const form = reactive({
  username: '',
  password: '',
  display_name: '',
  email: '',
  status: 'active',
  department_id: '',
  roles: ['viewer'] as string[],
})

const roleOptions = computed(() => roles.options)
const departmentOptions = computed(() => flattenDepartments(departments.value))

async function loadDepartments(): Promise<void> {
  try {
    const data = await departmentApi.list()
    departments.value = data.items
  } catch {
    departments.value = []
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await userApi.list({
      keyword: query.keyword || undefined,
      status: query.status || undefined,
      department_id: query.department_id || undefined,
      page: query.page,
      page_size: query.page_size,
    })
    rows.value = data.items
    total.value = data.total
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

function openCreate(): void {
  editing.value = null
  form.username = ''
  form.password = ''
  form.display_name = ''
  form.email = ''
  form.status = 'active'
  form.department_id = ''
  form.roles = ['viewer']
  dialogVisible.value = true
}

function openEdit(row: UserItem): void {
  editing.value = row
  form.username = row.username
  form.display_name = row.display_name
  form.email = row.email ?? ''
  form.status = row.status
  form.department_id = row.department_id ?? ''
  form.roles = [...row.roles]
  dialogVisible.value = true
}

async function save(): Promise<void> {
  if (!form.display_name.trim()) {
    ElMessage.warning('请填写显示名')
    return
  }
  if (!form.roles.length) {
    ElMessage.warning('至少选择一个角色')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await userApi.update(editing.value.id, {
        display_name: form.display_name.trim(),
        email: form.email.trim() || null,
        status: form.status,
        department_id: form.department_id || null,
        roles: form.roles,
      })
      ElMessage.success('已保存')
    } else {
      await userApi.create({
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        email: form.email.trim() || null,
        department_id: form.department_id || null,
        roles: form.roles,
      })
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function resetPassword(row: UserItem): Promise<void> {
  let password: string
  try {
    const result = await ElMessageBox.prompt(
      `为 ${row.display_name} 设置新密码（至少 8 位），该用户所有令牌将失效`,
      '重置密码',
      {
        confirmButtonText: '重置',
        cancelButtonText: '取消',
        inputType: 'password',
        inputValidator: (value) => (value && value.length >= 8 ? true : '密码至少 8 位'),
      },
    )
    password = result.value
  } catch {
    return
  }
  try {
    await userApi.resetPassword(row.id, password)
    ElMessage.success('密码已重置')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(() => {
  void load()
  void loadDepartments()
  void roles.ensureLoaded()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">用户管理</h1>
        <p class="page-sub">角色决定权限：管理员全量、审批人只审批、贡献者可写可审候选、只读仅浏览。</p>
      </div>
      <el-button type="primary" @click="openCreate">新建用户</el-button>
    </div>

    <div class="card">
      <div class="card-pad toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索用户名 / 显示名"
          clearable
          style="width: 220px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 120px">
          <el-option label="启用" value="active" />
          <el-option label="停用" value="disabled" />
        </el-select>
        <el-select
          v-model="query.department_id"
          placeholder="全部部门"
          clearable
          filterable
          style="width: 180px"
          @change="search"
        >
          <el-option
            v-for="item in departmentOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-button type="primary" plain @click="search">查询</el-button>
        <div class="spacer" />
        <span class="muted small">共 {{ total }} 个账号</span>
      </div>

      <el-table v-loading="loading" :data="rows">
        <el-table-column label="用户" min-width="200">
          <template #default="{ row }: { row: UserItem }">
            <div class="user-cell">
              <el-avatar :size="30" class="avatar">{{ row.display_name.slice(0, 1) }}</el-avatar>
              <div>
                <div class="name">
                  {{ row.display_name }}
                  <el-tag v-if="row.id === auth.user?.id" size="small" effect="plain">我</el-tag>
                </div>
                <div class="muted small">@{{ row.username }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="200">
          <template #default="{ row }: { row: UserItem }">
            <div class="row">
              <el-tag v-for="role in row.roles" :key="role" size="small" effect="plain">
                {{ roles.label(role) }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="部门" min-width="130">
          <template #default="{ row }: { row: UserItem }">
            <span class="muted small">{{ row.department_name ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="180">
          <template #default="{ row }: { row: UserItem }">
            <span class="muted small">{{ row.email ?? '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }: { row: UserItem }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
              {{ row.status === 'active' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" width="150">
          <template #default="{ row }: { row: UserItem }">
            <span class="muted small">{{ formatTime(row.last_login_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }: { row: UserItem }">
            <span class="muted small">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="right">
          <template #default="{ row }: { row: UserItem }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" @click="resetPassword(row)">重置密码</el-button>
          </template>
        </el-table-column>
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

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '新建用户'" width="520px">
      <el-form label-position="top">
        <div class="form-row">
          <el-form-item label="用户名">
            <el-input v-model="form.username" :disabled="Boolean(editing)" placeholder="登录名" />
          </el-form-item>
          <el-form-item label="显示名">
            <el-input v-model="form.display_name" placeholder="张三" maxlength="128" />
          </el-form-item>
        </div>
        <el-form-item v-if="!editing" label="初始密码">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="name@example.com" />
        </el-form-item>
        <el-form-item label="所属部门">
          <el-select
            v-model="form.department_id"
            placeholder="未分配部门"
            clearable
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="item in departmentOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-checkbox-group v-model="form.roles">
            <el-checkbox v-for="role in roleOptions" :key="role.value" :value="role.value">
              {{ role.label }}
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-if="editing" label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="disabled">停用</el-radio>
          </el-radio-group>
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
.user-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar {
  background: linear-gradient(135deg, #4f46e5, #7c3aed);
  font-size: 12px;
}

.name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}

.pager {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--line);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
</style>
