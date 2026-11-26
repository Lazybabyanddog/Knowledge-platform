<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { errorMessage } from '@/api/http'
import { parseApi } from '@/api/parse'
import type {
  AliasCardinality,
  AliasConfigView,
  AliasKind,
  AliasRule,
  ChunkStrategy,
  DocType,
  ParseEngine,
  ParseStrategy,
} from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { DOC_TYPE, formatTime } from '@/utils/format'

const auth = useAuthStore()

const MIN_CHUNK_SIZE = 100
const MAX_CHUNK_SIZE = 4000
const DEFAULT_CHUNK_SIZE = 800

const MIN_MAX_LEN = 2
const MAX_MAX_LEN = 60
const MAX_ALIAS_RULES = 20
const MAX_TRIGGERS = 20
const MAX_TRIGGER_LEN = 12
const MAX_REGEX_LEN = 200

const CHUNK_STRATEGIES: { value: ChunkStrategy; label: string; hint: string }[] = [
  { value: 'block_merge', label: '块合并（默认）', hint: '相邻正文块合并到长度上限，标题作为前缀。' },
  { value: 'fixed_window', label: '固定窗口', hint: '按长度窗口切分，相邻片段可保留重叠。' },
  { value: 'heading_section', label: '按标题分节', hint: '以标题为边界成节，超长节内再按窗口切分。' },
]

const ALIAS_KINDS: { value: AliasKind; label: string; hint: string }[] = [
  { value: 'ALIAS', label: 'ALIAS（简称）', hint: '简称与全称是同一对象，抽到即可写成确定对照。' },
  { value: 'ROLE', label: 'ROLE（角色称谓）', hint: '原告、被告等角色在本案内前向绑定，可写成确定对照。' },
  {
    value: 'RELATION',
    label: 'RELATION（关联方）',
    hint: '母子公司等一对多称谓只产出候选，写成"可能指"，永不判为确定。',
  },
]

const CARDINALITIES: { value: AliasCardinality; label: string }[] = [
  { value: 'ONE', label: 'ONE（唯一目标）' },
  { value: 'MANY', label: 'MANY（可能有多个）' },
  { value: 'UNKNOWN', label: 'UNKNOWN（按多个保守处理）' },
]

/** 表单里的规则用逗号分隔的字符串编辑，保存时再拆成数组 */
interface AliasRuleDraft {
  name: string
  kind: AliasKind
  triggers: string
  suffixes: string
  wrappers: string
  stop_words: string
  max_len: number
  target_cardinality: AliasCardinality
  priority: number
  regex: string
}

// 接口不可用时仍能编辑存量策略，取值与后端注册表保持一致
const FALLBACK_ENGINES: ParseEngine[] = [
  { name: 'pdf_layout', display_name: 'PDF 版面', doc_types: ['pdf'] },
  { name: 'ocr', display_name: '图片文字识别', doc_types: ['image', 'pdf'] },
  { name: 'office', display_name: 'Office 文档', doc_types: ['office'] },
  { name: 'plain_text', display_name: '纯文本', doc_types: ['text'] },
  { name: 'html_dom', display_name: 'HTML 结构解析', doc_types: ['html'] },
  { name: 'court_guide_case', display_name: '法院指导案例解析', doc_types: ['html'] },
  { name: 'mineru', display_name: 'MinerU（版面解析）', doc_types: ['pdf'] },
]

const loading = ref(false)
const rows = ref<ParseStrategy[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<ParseStrategy | null>(null)
const engines = ref<ParseEngine[]>(FALLBACK_ENGINES)

const form = reactive({
  name: '',
  doc_type: 'text' as DocType,
  engine: 'plain_text',
  config: '{}',
  chunk_strategy: 'block_merge' as ChunkStrategy,
  chunk_size: DEFAULT_CHUNK_SIZE,
  chunk_overlap: 0,
  alias_enabled: true,
  alias_use_builtin: true,
  alias_rules: [] as AliasRuleDraft[],
  is_default: false,
})

const engineOptions = computed(() => {
  const matched = engines.value.filter((item) => item.doc_types.includes(form.doc_type))
  // 修改存量策略时保留当前引擎，避免因 doc_type 过滤导致下拉框变空
  if (form.engine && !matched.some((item) => item.name === form.engine)) {
    const current = engines.value.find((item) => item.name === form.engine)
    return current ? [current, ...matched] : matched
  }
  return matched
})

const chunkHint = computed(
  () => CHUNK_STRATEGIES.find((item) => item.value === form.chunk_strategy)?.hint ?? '',
)

const canAddAliasRule = computed(
  () => form.alias_enabled && form.alias_rules.length < MAX_ALIAS_RULES,
)

/** 脏配置在后端返回空对象，这里单独标出，避免误当成"已关闭" */
function aliasBroken(alias: AliasConfigView | null | undefined): boolean {
  return !alias || Object.keys(alias).length === 0
}

function aliasSummary(alias: AliasConfigView | null | undefined): string {
  if (!alias || alias.enabled === false) return '未启用'
  const parts = [`${alias.rules?.length ?? 0} 条自定义`]
  if (alias.use_builtin) parts.push('含内置')
  return parts.join(' · ')
}

function chunkLabel(strategy: string): string {
  return CHUNK_STRATEGIES.find((item) => item.value === strategy)?.label ?? strategy
}

function kindHint(kind: AliasKind): string {
  return ALIAS_KINDS.find((item) => item.value === kind)?.hint ?? ''
}

function otherConfig(config: Record<string, unknown>): string {
  const rest = { ...config }
  delete rest.chunk
  delete rest.alias
  return Object.keys(rest).length ? JSON.stringify(rest) : '—'
}

function splitList(text: string): string[] {
  return [...new Set(text.split(/[,，、]/).map((item) => item.trim()).filter(Boolean))]
}

function joinList(items: string[] | null | undefined): string {
  return (items ?? []).join('、')
}

function emptyAliasRule(): AliasRuleDraft {
  return {
    name: '',
    kind: 'ALIAS',
    triggers: '',
    suffixes: '',
    wrappers: '',
    stop_words: '',
    max_len: 40,
    target_cardinality: 'ONE',
    priority: 50,
    regex: '',
  }
}

function draftFromRule(rule: AliasRule): AliasRuleDraft {
  return {
    name: rule.name,
    kind: rule.kind,
    triggers: joinList(rule.triggers),
    suffixes: joinList(rule.shapes?.suffixes),
    wrappers: joinList(rule.shapes?.wrappers),
    stop_words: joinList(rule.shapes?.stop_words),
    max_len: rule.shapes?.max_len ?? 40,
    target_cardinality: rule.target_cardinality,
    priority: rule.priority,
    regex: rule.regex ?? '',
  }
}

function fillAlias(alias: AliasConfigView | null | undefined): void {
  const usable = alias && Object.keys(alias).length ? alias : null
  form.alias_enabled = usable?.enabled ?? true
  form.alias_use_builtin = usable?.use_builtin ?? true
  form.alias_rules = (usable?.rules ?? []).map(draftFromRule)
}

async function loadEngines(): Promise<void> {
  try {
    const data = await parseApi.engines()
    if (data.items.length) engines.value = data.items
  } catch {
    engines.value = FALLBACK_ENGINES
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await parseApi.strategies()
    rows.value = data.items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function resetChunk(): void {
  form.chunk_strategy = 'block_merge'
  form.chunk_size = DEFAULT_CHUNK_SIZE
  form.chunk_overlap = 0
}

function addAliasRule(): void {
  if (!canAddAliasRule.value) return
  form.alias_rules.push(emptyAliasRule())
}

function removeAliasRule(index: number): void {
  form.alias_rules.splice(index, 1)
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.doc_type = 'text'
  form.engine = engineOptions.value[0]?.name ?? 'plain_text'
  form.config = '{}'
  resetChunk()
  fillAlias(null)
  form.is_default = false
  dialogVisible.value = true
}

function openEdit(strategy: ParseStrategy): void {
  const config = { ...(strategy.config ?? {}) }
  const chunk = strategy.chunk ?? { strategy: 'block_merge', size: DEFAULT_CHUNK_SIZE, overlap: 0 }
  // 切块与词典各有表单，JSON 里只保留其它杂项，避免两处配置互相覆盖
  delete config.chunk
  delete config.alias

  editing.value = strategy
  form.name = strategy.name
  form.doc_type = strategy.doc_type
  form.engine = strategy.engine
  form.config = JSON.stringify(config, null, 2)
  form.chunk_strategy = chunk.strategy
  form.chunk_size = chunk.size
  form.chunk_overlap = chunk.overlap
  fillAlias(strategy.alias)
  form.is_default = strategy.is_default
  dialogVisible.value = true
}

function validateAlias(): string | null {
  if (!form.alias_enabled) return null
  if (form.alias_rules.length > MAX_ALIAS_RULES) {
    return `别名规则不得超过 ${MAX_ALIAS_RULES} 条`
  }
  const seen = new Set<string>()
  for (const [index, rule] of form.alias_rules.entries()) {
    const label = `第 ${index + 1} 条规则`
    const name = rule.name.trim()
    if (!name) return `${label}缺少名称`
    if (name.length > 64) return `${label}名称不得超过 64 字`
    if (seen.has(name)) return `规则名重复：${name}`
    seen.add(name)
    if (!ALIAS_KINDS.some((item) => item.value === rule.kind)) {
      return `规则「${name}」类型非法`
    }
    const triggers = splitList(rule.triggers)
    if (!triggers.length) return `规则「${name}」缺少触发词`
    if (triggers.length > MAX_TRIGGERS) {
      return `规则「${name}」触发词不得超过 ${MAX_TRIGGERS} 个`
    }
    const tooLong = triggers.find((item) => item.length > MAX_TRIGGER_LEN)
    if (tooLong) return `规则「${name}」触发词过长（≤${MAX_TRIGGER_LEN} 字）：${tooLong}`
    const hasShape = Boolean(
      splitList(rule.suffixes).length ||
        splitList(rule.wrappers).length ||
        splitList(rule.stop_words).length,
    )
    if (!hasShape && !rule.regex.trim()) {
      return `规则「${name}」至少需要一种名称形态（后缀 / 成对标记 / 停顿词）或正则`
    }
    if (
      !Number.isInteger(rule.max_len) ||
      rule.max_len < MIN_MAX_LEN ||
      rule.max_len > MAX_MAX_LEN
    ) {
      return `规则「${name}」的长度上限需在 ${MIN_MAX_LEN}-${MAX_MAX_LEN} 之间`
    }
    if (!Number.isInteger(rule.priority) || rule.priority < 0 || rule.priority > 100) {
      return `规则「${name}」的优先级需在 0-100 之间`
    }
    const regex = rule.regex.trim()
    if (regex) {
      if (regex.length > MAX_REGEX_LEN) {
        return `规则「${name}」的正则不得超过 ${MAX_REGEX_LEN} 字符`
      }
      if (!regex.includes('(?P<surface>')) {
        return `规则「${name}」的正则必须包含命名组 (?P<surface>...)`
      }
    }
  }
  return null
}

function aliasPayload(): Record<string, unknown> {
  return {
    enabled: form.alias_enabled,
    use_builtin: form.alias_use_builtin,
    rules: form.alias_rules.map((rule) => ({
      name: rule.name.trim(),
      kind: rule.kind,
      triggers: splitList(rule.triggers),
      shapes: {
        suffixes: splitList(rule.suffixes),
        wrappers: splitList(rule.wrappers),
        stop_words: splitList(rule.stop_words),
        max_len: rule.max_len,
      },
      target_cardinality: rule.target_cardinality,
      priority: rule.priority,
      regex: rule.regex.trim() || null,
    })),
  }
}

async function save(): Promise<void> {
  let config: Record<string, unknown>
  try {
    config = JSON.parse(form.config || '{}') as Record<string, unknown>
  } catch {
    ElMessage.error('配置不是合法 JSON')
    return
  }
  if (!Number.isInteger(form.chunk_size) || form.chunk_size < MIN_CHUNK_SIZE || form.chunk_size > MAX_CHUNK_SIZE) {
    ElMessage.error(`切块长度需在 ${MIN_CHUNK_SIZE} 到 ${MAX_CHUNK_SIZE} 之间`)
    return
  }
  const chunk: Record<string, number | string> = {
    strategy: form.chunk_strategy,
    size: form.chunk_size,
  }
  if (form.chunk_strategy === 'fixed_window') {
    if (!Number.isInteger(form.chunk_overlap) || form.chunk_overlap < 0) {
      ElMessage.error('重叠长度不能为负数')
      return
    }
    if (form.chunk_overlap > Math.floor(form.chunk_size / 2)) {
      ElMessage.error(`重叠长度不能超过切块长度的一半（${Math.floor(form.chunk_size / 2)}）`)
      return
    }
    chunk.overlap = form.chunk_overlap
  }
  config.chunk = chunk
  const aliasError = validateAlias()
  if (aliasError) {
    ElMessage.error(aliasError)
    return
  }
  // 表单是词典的唯一入口：JSON 里手写的 alias 一律丢弃，避免两份配置打架
  config.alias = aliasPayload()

  saving.value = true
  try {
    if (editing.value) {
      await parseApi.updateStrategy(editing.value.id, {
        name: form.name.trim(),
        engine: form.engine,
        config,
        is_default: form.is_default,
      })
      ElMessage.success('已保存，生成新版本')
    } else {
      await parseApi.createStrategy({
        name: form.name.trim(),
        doc_type: form.doc_type,
        engine: form.engine,
        config,
        is_default: form.is_default,
      })
      ElMessage.success('策略已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function remove(strategy: ParseStrategy): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除策略「${strategy.name}」吗？该策略的全部版本都会停用并从列表移除，历史任务仍保留执行时的策略快照。` +
        (strategy.is_default ? ' 它是当前默认策略，删除后需重新为该文档类型指定默认策略。' : ''),
      '删除解析策略',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const result = await parseApi.deleteStrategy(strategy.id)
    ElMessage.success(`已删除，停用了 ${result.disabled_versions} 个版本`)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(() => {
  void loadEngines()
  void load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">解析策略</h1>
        <p class="page-sub">按文档类型选择引擎与参数；修改会生成新版本，历史版本保留不影响已跑任务。</p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" @click="openCreate">新建策略</el-button>
    </div>

    <div class="card">
      <el-table v-loading="loading" :data="rows">
        <el-table-column label="名称" min-width="220">
          <template #default="{ row }: { row: ParseStrategy }">
            <div class="row">
              <strong>{{ row.name }}</strong>
              <el-tag v-if="row.is_default" size="small" type="success">默认</el-tag>
            </div>
            <div class="mono muted small">{{ row.strategy_key.slice(0, 12) }}…</div>
          </template>
        </el-table-column>
        <el-table-column label="文档类型" width="110">
          <template #default="{ row }: { row: ParseStrategy }">
            {{ DOC_TYPE[row.doc_type] ?? row.doc_type }}
          </template>
        </el-table-column>
        <el-table-column prop="engine" label="引擎" width="150" />
        <el-table-column label="版本" width="80">
          <template #default="{ row }: { row: ParseStrategy }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="切块" min-width="190">
          <template #default="{ row }: { row: ParseStrategy }">
            <div>{{ chunkLabel(row.chunk.strategy) }}</div>
            <div class="mono muted small">
              长度 {{ row.chunk.size
              }}<span v-if="row.chunk.overlap"> · 重叠 {{ row.chunk.overlap }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="名词典" min-width="170">
          <template #default="{ row }: { row: ParseStrategy }">
            <el-tag v-if="aliasBroken(row.alias)" size="small" type="warning">配置异常</el-tag>
            <template v-else>
              <el-tag :type="row.alias.enabled ? 'success' : 'info'" size="small">
                {{ row.alias.enabled ? '启用' : '关闭' }}
              </el-tag>
              <div class="muted small">{{ aliasSummary(row.alias) }}</div>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="其他配置" min-width="160">
          <template #default="{ row }: { row: ParseStrategy }">
            <span class="mono muted small">{{ otherConfig(row.config) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }: { row: ParseStrategy }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">
              {{ row.status === 'active' ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }: { row: ParseStrategy }">
            <span class="muted small">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="right">
          <template #default="{ row }: { row: ParseStrategy }">
            <el-button v-if="auth.isAdmin" link type="primary" @click="openEdit(row)">修改</el-button>
            <el-button v-if="auth.isAdmin" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有策略，新建一个默认策略" :image-size="70" />
        </template>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '修改策略（生成新版本）' : '新建策略'" width="760px">
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="64" placeholder="例如：PDF 高精度解析" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="文档类型">
            <el-select v-model="form.doc_type" :disabled="Boolean(editing)" style="width: 100%">
              <el-option v-for="(label, value) in DOC_TYPE" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="引擎">
            <el-select v-model="form.engine" filterable style="width: 100%">
              <el-option
                v-for="engine in engineOptions"
                :key="engine.name"
                :label="`${engine.display_name}（${engine.name}）`"
                :value="engine.name"
              />
            </el-select>
          </el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="切块策略">
            <el-select v-model="form.chunk_strategy" style="width: 100%">
              <el-option
                v-for="item in CHUNK_STRATEGIES"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="切块长度">
            <el-input-number
              v-model="form.chunk_size"
              :min="MIN_CHUNK_SIZE"
              :max="MAX_CHUNK_SIZE"
              :step="100"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>
        </div>
        <el-form-item v-if="form.chunk_strategy === 'fixed_window'" label="相邻片段重叠长度">
          <el-input-number
            v-model="form.chunk_overlap"
            :min="0"
            :max="Math.floor(form.chunk_size / 2)"
            :step="20"
            controls-position="right"
            style="width: 100%"
          />
        </el-form-item>
        <p class="chunk-hint muted small">{{ chunkHint }}</p>

        <el-divider content-position="left">文档名词典</el-divider>
        <div class="alias-head">
          <el-switch v-model="form.alias_enabled" active-text="启用词典" />
          <el-switch
            v-model="form.alias_use_builtin"
            :disabled="!form.alias_enabled"
            active-text="叠加内置规则"
          />
        </div>
        <p class="chunk-hint muted small">
          建索引时按解析块抽取；问答只注入片段内实际出现的条目，不改正文、不进引用。内置规则：简称标记、角色称谓、关联方称谓。
        </p>
        <template v-if="form.alias_enabled">
          <div v-for="(rule, index) in form.alias_rules" :key="index" class="alias-rule">
            <div class="alias-rule-head">
              <span class="alias-rule-title">规则 {{ index + 1 }}</span>
              <el-button link type="danger" @click="removeAliasRule(index)">移除</el-button>
            </div>
            <div class="form-row">
              <el-form-item label="规则名">
                <el-input v-model="rule.name" maxlength="64" placeholder="例如：合同简称" />
              </el-form-item>
              <el-form-item label="类型">
                <el-select v-model="rule.kind" style="width: 100%">
                  <el-option
                    v-for="item in ALIAS_KINDS"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>
            </div>
            <p class="chunk-hint muted small">{{ kindHint(rule.kind) }}</p>
            <el-form-item label="触发词（逗号分隔，只定位左边界）">
              <el-input v-model="rule.triggers" placeholder="以下简称，简称，下称" />
            </el-form-item>
            <div class="form-row">
              <el-form-item label="机构后缀">
                <el-input v-model="rule.suffixes" placeholder="公司，分公司，集团" />
              </el-form-item>
              <el-form-item label="成对标记">
                <el-input v-model="rule.wrappers" placeholder="《，》，（，）" />
              </el-form-item>
            </div>
            <div class="form-row">
              <el-form-item label="停顿词">
                <el-input v-model="rule.stop_words" placeholder="的规定，的约定，为准" />
              </el-form-item>
              <el-form-item label="长度上限">
                <el-input-number
                  v-model="rule.max_len"
                  :min="MIN_MAX_LEN"
                  :max="MAX_MAX_LEN"
                  controls-position="right"
                  style="width: 100%"
                />
              </el-form-item>
            </div>
            <div class="form-row">
              <el-form-item label="目标基数">
                <el-select v-model="rule.target_cardinality" style="width: 100%">
                  <el-option
                    v-for="item in CARDINALITIES"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="优先级（0-100）">
                <el-input-number
                  v-model="rule.priority"
                  :min="0"
                  :max="100"
                  controls-position="right"
                  style="width: 100%"
                />
              </el-form-item>
            </div>
            <el-form-item label="正则（可选，必须含 (?P&lt;surface&gt;...)）">
              <el-input
                v-model="rule.regex"
                class="mono-input"
                placeholder="(?P&lt;surface&gt;[^，。]{2,20})"
              />
            </el-form-item>
          </div>
          <div class="alias-actions">
            <el-button :disabled="!canAddAliasRule" @click="addAliasRule">添加规则</el-button>
            <span class="muted small">{{ form.alias_rules.length }} / {{ MAX_ALIAS_RULES }}</span>
          </div>
        </template>

        <el-form-item label="其他配置（JSON）">
          <el-input v-model="form.config" type="textarea" :rows="4" class="mono-input" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.is_default">设为该文档类型的默认策略</el-checkbox>
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

.chunk-hint {
  margin: -4px 0 12px;
  line-height: 1.5;
}

.alias-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.alias-rule {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
}

.alias-rule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.alias-rule-title {
  font-size: 13px;
  font-weight: 600;
}

.alias-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0 16px;
}

.mono-input :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
}
</style>
