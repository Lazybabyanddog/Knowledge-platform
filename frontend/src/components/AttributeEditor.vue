<script setup lang="ts">
/**
 * 属性定义编辑器：实体类与关系类型共用。
 *
 * 为什么不再让人手写 JSON：属性定义是抽取的硬约束（类型、枚举、必填、示例都会进
 * 提示词），一个拼错的 type 会让整批抽取里该字段被丢掉。这里用表单把九个字段拆开，
 * 非法组合（枚举没取值、数组没元素类型）当场给出提示。
 *
 * 传了 `identityKeys` 才显示"标识"勾选：只有实体类需要跨文档身份。
 */
import { computed, ref, watch } from 'vue'

import type { AttributeDef, AttributeType } from '@/api/types'

interface Row {
  key: number
  code: string
  name: string
  type: AttributeType
  description: string
  hint: string
  examples: string[]
  required: boolean
  enumValues: string[]
  itemsType: AttributeType | null
}

const props = withDefaults(
  defineProps<{
    modelValue: Record<string, AttributeDef>
    identityKeys?: string[]
    disabled?: boolean
  }>(),
  { identityKeys: undefined, disabled: false },
)

const emit = defineEmits<{
  (event: 'update:modelValue', value: Record<string, AttributeDef>): void
  (event: 'update:identityKeys', value: string[]): void
}>()

const TYPE_OPTIONS: { value: AttributeType; label: string }[] = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数值' },
  { value: 'date', label: '日期' },
  { value: 'boolean', label: '布尔' },
  { value: 'enum', label: '枚举' },
  { value: 'array', label: '数组' },
]
const ITEM_TYPE_OPTIONS = TYPE_OPTIONS.filter((item) => item.value !== 'array')

let seq = 0
const rows = ref<Row[]>([])
const collapsed = ref<Set<number>>(new Set())
// 高级模式：同一份数据的 JSON 视图，给批量粘贴用；语法错误时不动表单，也不触发保存。
const advanced = ref(false)
const advancedText = ref('')
const advancedError = ref('')
// 回灌父组件"上一次的产出"时跳过重建，否则每次输入都会重排 DOM、光标跳走。
let lastEmitted: string | null = null

function toRow(code: string, def: Partial<AttributeDef>): Row {
  return {
    key: ++seq,
    code: def.code ?? code,
    name: def.name ?? code,
    type: (def.type as AttributeType) ?? 'string',
    description: def.description ?? '',
    hint: def.hint ?? '',
    examples: [...(def.examples ?? [])],
    required: Boolean(def.required),
    enumValues: [...(def.enum_values ?? [])],
    itemsType: (def.items_type as AttributeType | null) ?? null,
  }
}

function toRows(map: Record<string, AttributeDef> | undefined): Row[] {
  const entries = Object.entries(map ?? {})
  if (!entries.length) return []
  return entries.map(([code, def]) => toRow(code, def ?? ({} as AttributeDef)))
}

rows.value = toRows(props.modelValue)
const identitySet = computed(() => new Set(props.identityKeys ?? []))

watch(
  () => props.modelValue,
  (value) => {
    if (lastEmitted && JSON.stringify(value ?? {}) === lastEmitted) return
    rows.value = toRows(value)
  },
  { deep: true },
)

function buildMap(): Record<string, AttributeDef> {
  const map: Record<string, AttributeDef> = {}
  for (const row of rows.value) {
    const code = row.code.trim()
    if (!code) continue
    const name = row.name.trim() || code
    map[code] = {
      code,
      name,
      type: row.type,
      description: row.description.trim() || name,
      hint: row.hint.trim() || null,
      examples: row.examples.filter((item) => item.trim()),
      required: row.required,
      enum_values: row.type === 'enum' ? row.enumValues.filter((item) => item.trim()) : null,
      items_type: row.type === 'array' ? (row.itemsType ?? 'string') : null,
    }
  }
  return map
}

function emitChange(): void {
  const map = buildMap()
  lastEmitted = JSON.stringify(map)
  emit('update:modelValue', map)
  if (props.identityKeys === undefined) return
  // 标识键顺序跟表格行顺序走：上移/下移属性后保存的顺序与界面看到的一致。
  const ordered = rows.value
    .map((row) => row.code.trim())
    .filter((code) => code && identitySet.value.has(code))
  if (JSON.stringify(ordered) !== JSON.stringify(props.identityKeys)) {
    emit('update:identityKeys', ordered)
  }
}

function addRow(): void {
  rows.value.push(toRow('', {}))
  emitChange()
}

function moveRow(row: Row, offset: number): void {
  const index = rows.value.findIndex((item) => item.key === row.key)
  const target = index + offset
  if (index < 0 || target < 0 || target >= rows.value.length) return
  const next = [...rows.value]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  rows.value = next
  emitChange()
}

function toggleAdvanced(): void {
  advanced.value = !advanced.value
  if (!advanced.value) return
  advancedError.value = ''
  advancedText.value = JSON.stringify(buildMap(), null, 2)
  lastEmitted = JSON.stringify(buildMap())
}

function onAdvancedChange(): void {
  advancedError.value = ''
  let parsed: unknown
  try {
    parsed = JSON.parse(advancedText.value || '{}')
  } catch {
    advancedError.value = 'JSON 语法错误，确认修正后才会写回表单'
    return
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    advancedError.value = '属性定义必须是"编码 → 定义"的对象'
    return
  }
  rows.value = toRows(parsed as Record<string, AttributeDef>)
  emitChange()
}

function removeRow(row: Row): void {
  rows.value = rows.value.filter((item) => item.key !== row.key)
  if (identitySet.value.has(row.code.trim())) {
    emit(
      'update:identityKeys',
      [...identitySet.value].filter((code) => code !== row.code.trim()),
    )
  }
  emitChange()
}

function toggleDetail(row: Row): void {
  const next = new Set(collapsed.value)
  if (next.has(row.key)) next.delete(row.key)
  else next.add(row.key)
  collapsed.value = next
}

function toggleIdentity(row: Row, checked: boolean): void {
  const code = row.code.trim()
  if (!code) return
  const next = new Set(identitySet.value)
  if (checked) next.add(code)
  else next.delete(code)
  emit(
    'update:identityKeys',
    rows.value
      .map((item) => item.code.trim())
      .filter((item) => item && next.has(item)),
  )
}

function onTypeChange(row: Row): void {
  if (row.type === 'array' && !row.itemsType) row.itemsType = 'string'
  emitChange()
}

/** 保存前的整表校验：编码、描述、枚举取值、数组元素类型。返回错误文案，null 表示通过。 */
function validate(): string | null {
  if (advancedError.value) return '高级模式里的 JSON 还没修正'
  const seen = new Set<string>()
  for (const row of rows.value) {
    const code = row.code.trim()
    if (!code) return '有属性还没填编码'
    if (!/^[A-Za-z][A-Za-z0-9_]{1,63}$/.test(code)) {
      return `属性编码 ${code} 不合法：字母开头、2-64 位，仅字母/数字/下划线`
    }
    if (seen.has(code)) return `属性编码重复：${code}`
    seen.add(code)
    if (!row.description.trim()) return `属性 ${code} 缺少描述`
    if (row.type === 'enum' && !row.enumValues.filter((item) => item.trim()).length) {
      return `枚举属性 ${code} 至少要有一个取值`
    }
    if (row.type === 'array' && !row.itemsType) return `数组属性 ${code} 缺少元素类型`
  }
  return null
}

const summary = computed(() => {
  const total = rows.value.length
  if (!total) return '暂无属性'
  const identity = rows.value.filter((row) => identitySet.value.has(row.code.trim())).length
  return identity ? `共 ${total} 个属性，${identity} 个标识属性` : `共 ${total} 个属性`
})

defineExpose({ validate })
</script>

<template>
  <div class="attribute-editor">
    <div class="editor-head">
      <span class="muted small">{{ summary }}</span>
      <div class="row">
        <el-button size="small" :disabled="disabled" @click="addRow">添加属性</el-button>
        <el-button size="small" text :disabled="disabled" @click="toggleAdvanced">
          {{ advanced ? '返回表单' : 'JSON 高级模式' }}
        </el-button>
      </div>
    </div>

    <div v-if="advanced" class="advanced">
      <el-input
        v-model="advancedText"
        type="textarea"
        :rows="10"
        class="mono"
        :disabled="disabled"
        placeholder='{ "uscc": { "name": "统一社会信用代码", "type": "string", ... } }'
        @change="onAdvancedChange"
      />
      <div v-if="advancedError" class="advanced-error">{{ advancedError }}</div>
    </div>

    <el-empty
      v-if="!advanced && !rows.length"
      description="没有属性定义，抽取时不会采集该类型的字段"
      :image-size="56"
    />

    <div v-for="row in rows" v-show="!advanced" :key="row.key" class="attr-item">
      <div class="attr-main">
        <el-input
          v-model="row.code"
          class="attr-code mono"
          placeholder="编码，如 uscc"
          maxlength="64"
          :disabled="disabled"
          @change="emitChange"
        />
        <el-input
          v-model="row.name"
          placeholder="展示名"
          maxlength="64"
          :disabled="disabled"
          @change="emitChange"
        />
        <el-select v-model="row.type" class="attr-type" :disabled="disabled" @change="onTypeChange">
          <el-option
            v-for="item in TYPE_OPTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-checkbox
          v-if="identityKeys !== undefined"
          :model-value="identitySet.has(row.code.trim())"
          :disabled="disabled || !row.code.trim()"
          @change="(checked: boolean) => toggleIdentity(row, checked)"
        >
          标识
        </el-checkbox>
        <el-button link :disabled="disabled" @click="toggleDetail(row)">
          {{ collapsed.has(row.key) ? '展开' : '收起' }}
        </el-button>
        <el-button link :disabled="disabled" @click="moveRow(row, -1)">上移</el-button>
        <el-button link :disabled="disabled" @click="moveRow(row, 1)">下移</el-button>
        <el-button link type="danger" :disabled="disabled" @click="removeRow(row)">删除</el-button>
      </div>

      <div v-if="!collapsed.has(row.key)" class="attr-detail">
        <el-input
          v-model="row.description"
          type="textarea"
          :rows="2"
          placeholder="描述：写给抽取提示词看的判定说明，空则用展示名"
          maxlength="1000"
          :disabled="disabled"
          @change="emitChange"
        />
        <div class="attr-grid">
          <el-input
            v-model="row.hint"
            placeholder="抽取提示（可选）"
            maxlength="500"
            :disabled="disabled"
            @change="emitChange"
          />
          <el-select
            v-model="row.examples"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="示例值（最多 3 条，回车新增）"
            :multiple-limit="3"
            :disabled="disabled"
            @change="emitChange"
          >
            <el-option v-for="item in row.examples" :key="item" :label="item" :value="item" />
          </el-select>
          <el-select
            v-if="row.type === 'enum'"
            v-model="row.enumValues"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="枚举取值（回车新增）"
            :disabled="disabled"
            @change="emitChange"
          >
            <el-option v-for="item in row.enumValues" :key="item" :label="item" :value="item" />
          </el-select>
          <el-select
            v-if="row.type === 'array'"
            :model-value="row.itemsType ?? 'string'"
            placeholder="元素类型"
            :disabled="disabled"
            @update:model-value="
              (value: AttributeType) => {
                row.itemsType = value
                emitChange()
              }
            "
          >
            <el-option
              v-for="item in ITEM_TYPE_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-checkbox
            v-model="row.required"
            :disabled="disabled"
            @change="emitChange"
          >
            必填：抽不到就整条实体判为不完整
          </el-checkbox>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.attribute-editor {
  width: 100%;
}

.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.attr-item {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
}

.attr-main {
  display: grid;
  grid-template-columns: 150px 1fr 110px auto auto auto auto auto;
  gap: 8px;
  align-items: center;
}

.advanced-error {
  margin-top: 6px;
  color: var(--el-color-danger);
  font-size: 12px;
}

.attr-type {
  width: 110px;
}

.attr-detail {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.attr-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.mono :deep(input) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
}
</style>
