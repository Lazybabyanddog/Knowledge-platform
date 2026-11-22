<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { errorMessage } from '@/api/http'
import { ontologyApi } from '@/api/ontology'
import type { OntologyClass, OntologyRelation } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { formatTime } from '@/utils/format'

const auth = useAuthStore()

const tab = ref<'classes' | 'relations'>('classes')
const loading = ref(false)
const classes = ref<OntologyClass[]>([])
const relations = ref<OntologyRelation[]>([])
const version = ref(0)
const syncing = ref(false)

const classDialog = ref(false)
const classEditing = ref<OntologyClass | null>(null)
const classForm = reactive({
  code: '',
  name: '',
  description: '',
  parent_id: '',
  attributes: '{}',
  status: 'active',
})

const relationDialog = ref(false)
const relationEditing = ref<OntologyRelation | null>(null)
const relationForm = reactive({
  code: '',
  name: '',
  description: '',
  source_class_id: '',
  target_class_id: '',
  status: 'active',
})

const saving = ref(false)

const classOptions = computed(() =>
  classes.value.map((item) => ({ value: item.id, label: `${item.name}（${item.code}）` })),
)
const classMap = computed(() => Object.fromEntries(classes.value.map((item) => [item.id, item])))

async function load(): Promise<void> {
  loading.value = true
  try {
    const [classData, relationData] = await Promise.all([
      ontologyApi.classes(true),
      ontologyApi.relations(true),
    ])
    classes.value = classData.items
    relations.value = relationData.items
    version.value = classData.version
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openClassCreate(): void {
  classEditing.value = null
  classForm.code = ''
  classForm.name = ''
  classForm.description = ''
  classForm.parent_id = ''
  classForm.attributes = '{}'
  classForm.status = 'active'
  classDialog.value = true
}

function openClassEdit(row: OntologyClass): void {
  classEditing.value = row
  classForm.code = row.code
  classForm.name = row.name
  classForm.description = row.description ?? ''
  classForm.parent_id = row.parent_id ?? ''
  classForm.attributes = JSON.stringify(row.attributes ?? {}, null, 2)
  classForm.status = row.status
  classDialog.value = true
}

async function saveClass(): Promise<void> {
  let attributes: Record<string, unknown>
  try {
    attributes = JSON.parse(classForm.attributes || '{}') as Record<string, unknown>
  } catch {
    ElMessage.error('属性定义不是合法 JSON')
    return
  }
  saving.value = true
  try {
    const body = {
      name: classForm.name.trim(),
      description: classForm.description.trim() || null,
      parent_id: classForm.parent_id || null,
      attributes,
      status: classForm.status,
    }
    if (classEditing.value) {
      await ontologyApi.updateClass(classEditing.value.id, body)
      ElMessage.success('已保存（版本 +1）')
    } else {
      await ontologyApi.createClass({ ...body, code: classForm.code.trim() })
      ElMessage.success('实体类已创建')
    }
    classDialog.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

function openRelationCreate(): void {
  relationEditing.value = null
  relationForm.code = ''
  relationForm.name = ''
  relationForm.description = ''
  relationForm.source_class_id = ''
  relationForm.target_class_id = ''
  relationForm.status = 'active'
  relationDialog.value = true
}

function openRelationEdit(row: OntologyRelation): void {
  relationEditing.value = row
  relationForm.code = row.code
  relationForm.name = row.name
  relationForm.description = row.description ?? ''
  relationForm.source_class_id = row.source_class_id
  relationForm.target_class_id = row.target_class_id
  relationForm.status = row.status
  relationDialog.value = true
}

async function saveRelation(): Promise<void> {
  saving.value = true
  try {
    const body = {
      name: relationForm.name.trim(),
      description: relationForm.description.trim() || null,
      source_class_id: relationForm.source_class_id,
      target_class_id: relationForm.target_class_id,
      status: relationForm.status,
    }
    if (relationEditing.value) {
      await ontologyApi.updateRelation(relationEditing.value.id, body)
      ElMessage.success('已保存（版本 +1）')
    } else {
      await ontologyApi.createRelation({ ...body, code: relationForm.code.trim() })
      ElMessage.success('关系类型已创建')
    }
    relationDialog.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function sync(): Promise<void> {
  syncing.value = true
  try {
    const data = await ontologyApi.sync()
    ElMessage.success(`已同步 ${data.projected} 个类到图库`)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    syncing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">本体管理</h1>
        <p class="page-sub">
          定义实体类与关系类型，作为抽取的约束来源。当前本体版本
          <strong>v{{ version }}</strong>，变更会自动投影到图库。
        </p>
      </div>
      <div class="toolbar">
        <el-button :loading="syncing" @click="sync">同步到图库</el-button>
        <el-button
          v-if="auth.has('ontology:manage') && tab === 'classes'"
          type="primary"
          @click="openClassCreate"
        >
          新建实体类
        </el-button>
        <el-button
          v-if="auth.has('ontology:manage') && tab === 'relations'"
          type="primary"
          @click="openRelationCreate"
        >
          新建关系类型
        </el-button>
      </div>
    </div>

    <div class="card">
      <el-tabs v-model="tab" class="ontology-tabs">
        <el-tab-pane :label="`实体类 ${classes.length}`" name="classes">
          <el-table v-loading="loading" :data="classes">
            <el-table-column label="类" min-width="220">
              <template #default="{ row }: { row: OntologyClass }">
                <div class="row">
                  <strong>{{ row.name }}</strong>
                  <el-tag size="small" effect="plain" class="mono">{{ row.code }}</el-tag>
                  <el-tag v-if="row.status !== 'active'" size="small" type="info">停用</el-tag>
                </div>
                <div class="muted small">{{ row.description ?? '暂无描述' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="父类" width="140">
              <template #default="{ row }: { row: OntologyClass }">
                <span class="muted small">
                  {{ row.parent_id ? classMap[row.parent_id]?.name ?? row.parent_id.slice(0, 8) : '根类' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="属性" min-width="200">
              <template #default="{ row }: { row: OntologyClass }">
                <span class="mono muted small">{{ JSON.stringify(row.attributes) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="80">
              <template #default="{ row }: { row: OntologyClass }">v{{ row.version }}</template>
            </el-table-column>
            <el-table-column label="更新时间" width="150">
              <template #default="{ row }: { row: OntologyClass }">
                <span class="muted small">{{ formatTime(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="right">
              <template #default="{ row }: { row: OntologyClass }">
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="primary"
                  @click="openClassEdit(row)"
                >
                  修改
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="先定义实体类，抽取才有约束" :image-size="70" />
            </template>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`关系类型 ${relations.length}`" name="relations">
          <el-table v-loading="loading" :data="relations">
            <el-table-column label="关系" min-width="180">
              <template #default="{ row }: { row: OntologyRelation }">
                <div class="row">
                  <strong>{{ row.name }}</strong>
                  <el-tag size="small" effect="plain" class="mono">{{ row.code }}</el-tag>
                  <el-tag v-if="row.status !== 'active'" size="small" type="info">停用</el-tag>
                </div>
                <div class="muted small">{{ row.description ?? '暂无描述' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="起点 → 终点" min-width="220">
              <template #default="{ row }: { row: OntologyRelation }">
                <span class="muted">
                  {{ row.source_class_code }} <el-icon><Right /></el-icon> {{ row.target_class_code }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="80">
              <template #default="{ row }: { row: OntologyRelation }">v{{ row.version }}</template>
            </el-table-column>
            <el-table-column label="更新时间" width="150">
              <template #default="{ row }: { row: OntologyRelation }">
                <span class="muted small">{{ formatTime(row.updated_at) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" align="right">
              <template #default="{ row }: { row: OntologyRelation }">
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="primary"
                  @click="openRelationEdit(row)"
                >
                  修改
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="还没有关系类型" :image-size="70" />
            </template>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog
      v-model="classDialog"
      :title="classEditing ? '修改实体类' : '新建实体类'"
      width="560px"
    >
      <el-form label-position="top">
        <div class="form-row">
          <el-form-item label="编码">
            <el-input
              v-model="classForm.code"
              :disabled="Boolean(classEditing)"
              placeholder="小写字母开头，如 org"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="classForm.name" placeholder="组织" maxlength="64" />
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="classForm.description" type="textarea" :rows="2" maxlength="200" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="父类">
            <el-select v-model="classForm.parent_id" clearable filterable style="width: 100%">
              <el-option
                v-for="item in classOptions.filter((option) => option.value !== classEditing?.id)"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="classForm.status" style="width: 100%">
              <el-option label="启用" value="active" />
              <el-option label="停用" value="disabled" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="属性定义（JSON）">
          <el-input v-model="classForm.attributes" type="textarea" :rows="4" class="mono-input" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="classDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveClass">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="relationDialog"
      :title="relationEditing ? '修改关系类型' : '新建关系类型'"
      width="560px"
    >
      <el-form label-position="top">
        <div class="form-row">
          <el-form-item label="编码">
            <el-input
              v-model="relationForm.code"
              :disabled="Boolean(relationEditing)"
              placeholder="如 works_at"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item label="名称">
            <el-input v-model="relationForm.name" placeholder="任职" maxlength="64" />
          </el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="起点类">
            <el-select v-model="relationForm.source_class_id" filterable style="width: 100%">
              <el-option v-for="item in classOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="终点类">
            <el-select v-model="relationForm.target_class_id" filterable style="width: 100%">
              <el-option v-for="item in classOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="描述">
          <el-input v-model="relationForm.description" type="textarea" :rows="2" maxlength="200" />
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="relationForm.status" style="width: 160px">
            <el-option label="启用" value="active" />
            <el-option label="停用" value="disabled" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="relationDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRelation">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ontology-tabs {
  padding: 4px 20px 8px;
}

.ontology-tabs :deep(.el-tabs__header) {
  margin-bottom: 4px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.mono-input :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
}
</style>
