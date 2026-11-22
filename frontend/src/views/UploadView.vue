<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type UploadFile, type UploadUserFile } from 'element-plus'

import { documentApi } from '@/api/documents'
import { errorMessage } from '@/api/http'
import type { BatchUploadResponse } from '@/api/types'
import { SENSITIVITY, formatFileSize, statusMeta } from '@/utils/format'

const router = useRouter()

const mode = ref<'single' | 'batch'>('single')
const files = ref<UploadUserFile[]>([])
const progress = ref(0)
const uploading = ref(false)
const batchResult = ref<BatchUploadResponse | null>(null)

const form = reactive({
  title: '',
  summary: '',
  sensitivity: 'internal',
  tags: '',
})

const sensitivityOptions = Object.entries(SENSITIVITY).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

const singleFile = computed(() => files.value[0])

function onChange(file: UploadFile, list: UploadUserFile[]): void {
  files.value = list.slice(-(mode.value === 'single' ? 1 : 20))
  batchResult.value = null
  if (mode.value === 'single') {
    form.title = file.name.replace(/\.[^.]+$/, '')
  }
}

function onRemove(_file: UploadFile, list: UploadUserFile[]): void {
  files.value = list
}

function switchMode(): void {
  files.value = []
  progress.value = 0
  batchResult.value = null
}

function buildForm(): FormData {
  const data = new FormData()
  for (const item of files.value) {
    if (item.raw) data.append(mode.value === 'single' ? 'file' : 'files', item.raw)
  }
  data.append('sensitivity', form.sensitivity)
  if (form.tags.trim()) data.append('tags', form.tags.trim())
  if (mode.value === 'single') {
    if (form.title.trim()) data.append('title', form.title.trim())
    if (form.summary.trim()) data.append('summary', form.summary.trim())
  }
  return data
}

async function submit(): Promise<void> {
  if (files.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  uploading.value = true
  progress.value = 0
  try {
    const payload = buildForm()
    if (mode.value === 'single') {
      const document = await documentApi.upload(payload, (value) => {
        progress.value = value
      })
      ElMessage.success('上传成功，正在跳转详情')
      await router.push(`/documents/${document.id}`)
    } else {
      batchResult.value = await documentApi.uploadBatch(payload, (value) => {
        progress.value = value
      })
      ElMessage.success(`上传完成：成功 ${batchResult.value.succeeded} 个`)
      files.value = []
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">上传文档</h1>
        <p class="page-sub">支持 PDF / Office / 图片 / 纯文本 / HTML；上传后可在详情页触发解析与抽取。</p>
      </div>
      <el-button @click="router.push('/documents')">返回文档库</el-button>
    </div>

    <div class="upload-grid">
      <div class="card card-pad">
        <div class="toolbar" style="margin-bottom: 16px">
          <el-radio-group v-model="mode" @change="switchMode">
            <el-radio-button value="single">单个上传</el-radio-button>
            <el-radio-button value="batch">批量上传</el-radio-button>
          </el-radio-group>
          <div class="spacer" />
          <span class="muted small">单文件上限 200 MB</span>
        </div>

        <el-upload
          v-model:file-list="files"
          drag
          :auto-upload="false"
          :limit="mode === 'single' ? 1 : 20"
          :multiple="mode === 'batch'"
          :on-change="onChange"
          :on-remove="onRemove"
          :show-file-list="false"
        >
          <el-icon class="drop-icon"><UploadFilled /></el-icon>
          <div class="drop-text">
            将文件拖到此处，或 <em>点击选择</em>
          </div>
          <template #tip>
            <div class="muted small" style="margin-top: 8px">
              {{ mode === 'single' ? '单次一个文件，支持覆盖标题与摘要' : '最多一次 20 个文件，逐个独立处理' }}
            </div>
          </template>
        </el-upload>

        <div v-if="files.length" class="file-list">
          <div v-for="item in files" :key="item.uid" class="file-item">
            <el-icon class="file-icon"><Document /></el-icon>
            <div class="file-meta">
              <div class="file-name">{{ item.name }}</div>
              <div class="muted small">{{ formatFileSize(item.size) }}</div>
            </div>
            <el-button link type="danger" @click="onRemove(item as UploadFile, files.filter((f) => f.uid !== item.uid))">
              移除
            </el-button>
          </div>
        </div>

        <el-progress
          v-if="uploading || progress > 0"
          :percentage="progress"
          :stroke-width="8"
          style="margin-top: 16px"
        />
      </div>

      <div class="card">
        <div class="card-head">
          <h3>文档属性</h3>
        </div>
        <div class="card-pad">
          <el-form label-position="top">
            <template v-if="mode === 'single'">
              <el-form-item label="标题">
                <el-input v-model="form.title" placeholder="默认取文件名" maxlength="200" show-word-limit />
              </el-form-item>
              <el-form-item label="摘要">
                <el-input
                  v-model="form.summary"
                  type="textarea"
                  :rows="3"
                  maxlength="500"
                  show-word-limit
                  placeholder="一句话说明这份文档讲什么，便于检索"
                />
              </el-form-item>
            </template>

            <el-form-item label="密级">
              <el-select v-model="form.sensitivity" style="width: 100%">
                <el-option
                  v-for="item in sensitivityOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="标签">
              <el-input v-model="form.tags" placeholder="合同, 客户, 2026（逗号分隔）" />
            </el-form-item>

            <el-button type="primary" :loading="uploading" style="width: 100%" @click="submit">
              {{ uploading ? `上传中 ${progress}%` : '开始上传' }}
            </el-button>
          </el-form>

          <div v-if="singleFile" class="picked muted small">
            已选择：{{ singleFile.name }}（{{ formatFileSize(singleFile.size) }}）
          </div>
        </div>
      </div>
    </div>

    <div v-if="batchResult" class="card">
      <div class="card-head">
        <h3>批量结果</h3>
        <span class="muted small">
          成功 {{ batchResult.succeeded }} · 失败 {{ batchResult.failed }}
        </span>
      </div>
      <el-table :data="batchResult.items" size="small">
        <el-table-column prop="file_name" label="文件" min-width="220" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="row.success ? 'success' : 'danger'" size="small">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="220">
          <template #default="{ row }">
            <span v-if="row.success" class="muted small">已创建文档与首个版本</span>
            <span v-else class="danger-text small">{{ row.error?.message }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="right">
          <template #default="{ row }">
            <el-button
              v-if="row.document_id"
              link
              type="primary"
              @click="router.push(`/documents/${row.document_id}`)"
            >
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.upload-grid {
  display: grid;
  grid-template-columns: 1.35fr 1fr;
  gap: 16px;
  align-items: start;
}

.drop-icon {
  font-size: 44px;
  color: #b9bfd0;
  margin-bottom: 10px;
}

.drop-text {
  color: var(--ink-soft);
  font-size: 14px;
}

.drop-text em {
  color: var(--brand);
  font-style: normal;
  font-weight: 600;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #fbfcfd;
}

.file-icon {
  color: #98a2b3;
}

.file-meta {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picked {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--line);
}

.danger-text {
  color: #d92d20;
}

@media (max-width: 1100px) {
  .upload-grid {
    grid-template-columns: 1fr;
  }
}
</style>
