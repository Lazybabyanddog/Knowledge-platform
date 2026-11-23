<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { ontologyApi } from '@/api/ontology'
import type { AttributeDef, OntologyClass, OntologyRelation } from '@/api/types'
import AttributeEditor from '@/components/AttributeEditor.vue'
import { useAuthStore } from '@/stores/auth'
import { formatTime } from '@/utils/format'

const auth = useAuthStore()

// 与后端 _CODE_PATTERN 一致：字母开头，字母/数字/下划线，总长 2-64，大小写均可。
const CODE_PATTERN = /^[A-Za-z][A-Za-z0-9_]{1,63}$/

function validCode(code: string): boolean {
  if (CODE_PATTERN.test(code)) return true
  ElMessage.error('编码需以字母开头，仅含字母、数字与下划线，长度 2-64')
  return false
}

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
  attributes: {} as Record<string, AttributeDef>,
  identity_keys: [] as string[],
  status: 0 as 0 | 1,
})
const classEditor = ref<InstanceType<typeof AttributeEditor> | null>(null)

const relationDialog = ref(false)
const relationEditing = ref<OntologyRelation | null>(null)
const relationForm = reactive({
  code: '',
  name: '',
  description: '',
  source_class_id: '',
  target_class_id: '',
  attributes: {} as Record<string, AttributeDef>,
  status: 0 as 0 | 1,
})
const relationEditor = ref<InstanceType<typeof AttributeEditor> | null>(null)

const saving = ref(false)

const classOptions = computed(() =>
  classes.value.map((item) => ({ value: item.id, label: `${item.name}（${item.code}）` })),
)
const classMap = computed(() => Object.fromEntries(classes.value.map((item) => [item.id, item])))

type StatusFilter = 'all' | 0 | 1
const statusFilter = ref<StatusFilter>('all')

// 表格只展示筛选后的子集；classes / relations 保持全量，供父类解析与关系候选使用。
const visibleClasses = computed(() => {
  const filter = statusFilter.value
  return filter === 'all' ? classes.value : classes.value.filter((item) => item.status === filter)
})
const visibleRelations = computed(() => {
  const filter = statusFilter.value
  return filter === 'all' ? relations.value : relations.value.filter((item) => item.status === filter)
})

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
  classForm.attributes = {}
  classForm.identity_keys = []
  classForm.status = 0
  classDialog.value = true
}

function openClassEdit(row: OntologyClass): void {
  classEditing.value = row
  classForm.code = row.code
  classForm.name = row.name
  classForm.description = row.description ?? ''
  classForm.parent_id = row.parent_id ?? ''
  classForm.attributes = { ...(row.attributes ?? {}) }
  classForm.identity_keys = [...(row.identity_keys ?? [])]
  classForm.status = row.status
  classDialog.value = true
}

async function saveClass(): Promise<void> {
  const code = classForm.code.trim()
  if (!validCode(code)) return
  const invalid = classEditor.value?.validate() ?? null
  if (invalid) {
    ElMessage.error(invalid)
    return
  }
  // 标识属性必须真的存在：删掉属性后残留的标识键会让后端的删除校验直接 400
  const identityKeys = classForm.identity_keys.filter((key) => key in classForm.attributes)
  saving.value = true
  try {
    const body = {
      name: classForm.name.trim(),
      description: classForm.description.trim() || null,
      parent_id: classForm.parent_id || null,
      attributes: classForm.attributes,
      identity_keys: identityKeys,
      status: classForm.status,
    }
    if (classEditing.value) {
      await ontologyApi.updateClass(classEditing.value.id, body)
      ElMessage.success('已保存（版本 +1）')
    } else {
      await ontologyApi.createClass({ ...body, code })
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
  relationForm.attributes = {}
  relationForm.status = 0
  relationDialog.value = true
}

function openRelationEdit(row: OntologyRelation): void {
  relationEditing.value = row
  relationForm.code = row.code
  relationForm.name = row.name
  relationForm.description = row.description ?? ''
  relationForm.source_class_id = row.source_class_id
  relationForm.target_class_id = row.target_class_id
  relationForm.attributes = { ...(row.attributes ?? {}) }
  relationForm.status = row.status
  relationDialog.value = true
}

async function saveRelation(): Promise<void> {
  const code = relationForm.code.trim()
  if (!validCode(code)) return
  const invalid = relationEditor.value?.validate() ?? null
  if (invalid) {
    ElMessage.error(invalid)
    return
  }
  saving.value = true
  try {
    const body = {
      name: relationForm.name.trim(),
      description: relationForm.description.trim() || null,
      source_class_id: relationForm.source_class_id,
      target_class_id: relationForm.target_class_id,
      attributes: relationForm.attributes,
      status: relationForm.status,
    }
    if (relationEditing.value) {
      await ontologyApi.updateRelation(relationEditing.value.id, body)
      ElMessage.success('已保存（版本 +1）')
    } else {
      await ontologyApi.createRelation({ ...body, code })
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

async function removeClass(row: OntologyClass): Promise<void> {
  const children = classes.value.filter((item) => item.parent_id === row.id)
  try {
    await ElMessageBox.confirm(
      `确定删除实体类「${row.name}（${row.code}）」吗？` +
        (children.length
          ? `${children.length} 个子类会变成根类；`
          : '') +
        '删除后图库中的类节点也会一并移除，且不可恢复。',
      '删除实体类',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await ontologyApi.deleteClass(row.id)
    ElMessage.success('实体类已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function removeRelation(row: OntologyRelation): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除关系类型「${row.name}（${row.code}）」吗？该操作不可恢复，已抽取的关系事实保留。`,
      '删除关系类型',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await ontologyApi.deleteRelation(row.id)
    ElMessage.success('关系类型已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
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

    <div class="card ontology-card">
      <el-tabs v-model="tab" class="ontology-tabs">
        <el-tab-pane :label="`实体类 ${visibleClasses.length}/${classes.length}`" name="classes">
          <el-table v-loading="loading" :data="visibleClasses">
            <el-table-column label="类" min-width="220">
              <template #default="{ row }: { row: OntologyClass }">
                <div class="row">
                  <strong>{{ row.name }}</strong>
                  <el-tag size="small" effect="plain" class="mono">{{ row.code }}</el-tag>
                </div>
                <div class="muted small">{{ row.description ?? '暂无描述' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }: { row: OntologyClass }">
                <el-tag :type="row.status === 0 ? 'success' : 'info'" size="small">
                  {{ row.status === 0 ? '有效' : '失效' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="父类" width="140">
              <template #default="{ row }: { row: OntologyClass }">
                <span class="muted small">
                  {{ row.parent_id ? classMap[row.parent_id]?.name ?? row.parent_id.slice(0, 8) : '根类' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="属性 / 标识键" min-width="220">
              <template #default="{ row }: { row: OntologyClass }">
                <div class="row wrap">
                  <el-tag size="small" effect="plain">
                    {{ Object.keys(row.attributes ?? {}).length }} 个属性
                  </el-tag>
                  <el-tag
                    v-for="key in row.identity_keys ?? []"
                    :key="key"
                    size="small"
                    type="success"
                    effect="plain"
                    class="mono"
                  >
                    {{ key }}
                  </el-tag>
                  <span v-if="!(row.identity_keys ?? []).length" class="muted small">
                    未设标识键（按名称对齐）
                  </span>
                </div>
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
            <el-table-column label="操作" width="130" align="right">
              <template #default="{ row }: { row: OntologyClass }">
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="primary"
                  @click="openClassEdit(row)"
                >
                  修改
                </el-button>
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="danger"
                  @click="removeClass(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="
                  statusFilter === 'all' ? '先定义实体类，抽取才有约束' : '没有符合筛选条件的实体类'
                "
                :image-size="70"
              />
            </template>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="`关系类型 ${visibleRelations.length}/${relations.length}`" name="relations">
          <el-table v-loading="loading" :data="visibleRelations">
            <el-table-column label="关系" min-width="180">
              <template #default="{ row }: { row: OntologyRelation }">
                <div class="row">
                  <strong>{{ row.name }}</strong>
                  <el-tag size="small" effect="plain" class="mono">{{ row.code }}</el-tag>
                </div>
                <div class="muted small">{{ row.description ?? '暂无描述' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }: { row: OntologyRelation }">
                <el-tag :type="row.status === 0 ? 'success' : 'info'" size="small">
                  {{ row.status === 0 ? '有效' : '失效' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="起点 → 终点" min-width="220">
              <template #default="{ row }: { row: OntologyRelation }">
                <span class="muted">
                  {{ row.source_class_code }} <el-icon><Right /></el-icon> {{ row.target_class_code }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="属性" width="110">
              <template #default="{ row }: { row: OntologyRelation }">
                <span class="muted small">
                  {{ Object.keys(row.attributes ?? {}).length }} 个
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
            <el-table-column label="操作" width="130" align="right">
              <template #default="{ row }: { row: OntologyRelation }">
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="primary"
                  @click="openRelationEdit(row)"
                >
                  修改
                </el-button>
                <el-button
                  v-if="auth.has('ontology:manage')"
                  link
                  type="danger"
                  @click="removeRelation(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="
                  statusFilter === 'all' ? '还没有关系类型' : '没有符合筛选条件的关系类型'
                "
                :image-size="70"
              />
            </template>
          </el-table>
        </el-tab-pane>
      </el-tabs>
      <el-select v-model="statusFilter" class="status-filter" style="width: 132px">
        <el-option label="全部" value="all" />
        <el-option label="有效" :value="0" />
        <el-option label="失效" :value="1" />
      </el-select>
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
              placeholder="字母开头，如 org、Party"
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
              <el-option label="有效" :value="0" />
              <el-option label="失效" :value="1" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="属性定义">
          <AttributeEditor
            ref="classEditor"
            v-model="classForm.attributes"
            v-model:identity-keys="classForm.identity_keys"
          />
          <div class="muted small hint">
            勾选"标识"的属性会拼成跨文档身份键：同一份材料在不同文档里出现时按标识对齐成同一个实体；
            没勾（或抽不到值）则退化为按名称对齐。
          </div>
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
              placeholder="如 works_at、HAS_PLAINTIFF"
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
        <el-form-item label="关系属性">
          <AttributeEditor ref="relationEditor" v-model="relationForm.attributes" />
          <div class="muted small hint">
            关系属性写在边上，用于区分同端点同类型的多个实例（如同一人先后任职两家公司）。
          </div>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="relationForm.status" style="width: 160px">
            <el-option label="有效" :value="0" />
            <el-option label="失效" :value="1" />
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
/* el-tabs 没有右侧插槽，用外层 flex 让筛选框落在 tab 头右侧。 */
.ontology-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.ontology-tabs {
  flex: 1;
  min-width: 0;
  padding: 4px 20px 8px;
}

.ontology-tabs :deep(.el-tabs__header) {
  margin-bottom: 4px;
}

.status-filter {
  flex: none;
  margin: 8px 20px 0 0;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.hint {
  margin-top: 6px;
  line-height: 1.6;
}
</style>
