<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { errorMessage } from '@/api/http'
import { parseApi } from '@/api/parse'
import type { ParseStrategy } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { DOC_TYPE, formatTime } from '@/utils/format'

const auth = useAuthStore()

const loading = ref(false)
const rows = ref<ParseStrategy[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<ParseStrategy | null>(null)

const form = reactive({
  name: '',
  doc_type: 'text',
  engine: 'plain_text',
  config: '{}',
  is_default: false,
})

const engines = computed(() => {
  const set = new Set(rows.value.map((item) => item.engine))
  ;['plain_text', 'pdfminer', 'python_docx', 'ocr_tesseract', 'html_dom'].forEach((item) =>
    set.add(item),
  )
  return [...set]
})

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

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.doc_type = 'text'
  form.engine = engines.value[0] ?? 'plain_text'
  form.config = '{}'
  form.is_default = false
  dialogVisible.value = true
}

function openEdit(strategy: ParseStrategy): void {
  editing.value = strategy
  form.name = strategy.name
  form.doc_type = strategy.doc_type
  form.engine = strategy.engine
  form.config = JSON.stringify(strategy.config ?? {}, null, 2)
  form.is_default = strategy.is_default
  dialogVisible.value = true
}

async function save(): Promise<void> {
  let config: Record<string, unknown>
  try {
    config = JSON.parse(form.config || '{}') as Record<string, unknown>
  } catch {
    ElMessage.error('配置不是合法 JSON')
    return
  }
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

onMounted(load)
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
        <el-table-column label="配置" min-width="200">
          <template #default="{ row }: { row: ParseStrategy }">
            <span class="mono muted small">{{ JSON.stringify(row.config) }}</span>
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
        <el-table-column label="操作" width="90" align="right">
          <template #default="{ row }: { row: ParseStrategy }">
            <el-button v-if="auth.isAdmin" link type="primary" @click="openEdit(row)">修改</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有策略，新建一个默认策略" :image-size="70" />
        </template>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '修改策略（生成新版本）' : '新建策略'" width="560px">
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
            <el-select v-model="form.engine" filterable allow-create style="width: 100%">
              <el-option v-for="engine in engines" :key="engine" :label="engine" :value="engine" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="配置（JSON）">
          <el-input v-model="form.config" type="textarea" :rows="5" class="mono-input" />
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

.mono-input :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
}
</style>
