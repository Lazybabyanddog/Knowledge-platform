<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { documentApi } from '@/api/documents'
import { errorMessage } from '@/api/http'
import type { DocumentItem } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import {
  DOCUMENT_STATUS,
  DOC_TYPE,
  SENSITIVITY,
  formatFileSize,
  formatTime,
  statusMeta,
} from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const rows = ref<DocumentItem[]>([])
const total = ref(0)
const query = reactive({
  keyword: '',
  status: '',
  sensitivity: '',
  include_deleted: false,
  page: 1,
  page_size: 10,
})

const statusOptions = Object.entries(DOCUMENT_STATUS).map(([value, meta]) => ({
  value,
  label: meta.label,
}))
const sensitivityOptions = Object.entries(SENSITIVITY).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await documentApi.list({
      keyword: query.keyword || undefined,
      status: query.status || undefined,
      sensitivity: query.sensitivity || undefined,
      include_deleted: query.include_deleted || undefined,
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

function reset(): void {
  query.keyword = ''
  query.status = ''
  query.sensitivity = ''
  query.include_deleted = false
  search()
}

async function download(row: DocumentItem): Promise<void> {
  if (!row.current_version_id) {
    ElMessage.warning('该文档还没有可下载的版本')
    return
  }
  try {
    const data = await documentApi.download(row.id, row.current_version_id)
    window.open(data.url, '_blank', 'noopener')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function remove(row: DocumentItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除《${row.title}》？删除后可在“含已删除”中恢复。`, '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await documentApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function restore(row: DocumentItem): Promise<void> {
  try {
    await documentApi.restore(row.id)
    ElMessage.success('已恢复')
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
        <h1 class="page-title">文档库</h1>
        <p class="page-sub">上传原始文档，管理版本、密级与解析入口。</p>
      </div>
      <div class="toolbar">
        <el-button v-if="auth.has('document:write')" type="primary" @click="router.push('/documents/upload')">
          <el-icon><UploadFilled /></el-icon>
          <span>上传文档</span>
        </el-button>
      </div>
    </div>

    <div class="card">
      <div class="card-pad toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索标题 / 摘要"
          clearable
          style="width: 240px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 132px">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="query.sensitivity" placeholder="全部密级" clearable style="width: 132px">
          <el-option
            v-for="item in sensitivityOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-checkbox v-model="query.include_deleted" @change="search">含已删除</el-checkbox>
        <el-button type="primary" plain @click="search">查询</el-button>
        <el-button text @click="reset">重置</el-button>
        <div class="spacer" />
        <span class="muted small">共 {{ total }} 份文档</span>
      </div>

      <el-table v-loading="loading" :data="rows" style="width: 100%" @row-dblclick="(row: DocumentItem) => router.push(`/documents/${row.id}`)">
        <el-table-column label="文档" min-width="280">
          <template #default="{ row }: { row: DocumentItem }">
            <div class="doc-cell">
              <div class="doc-title">
                {{ row.title }}
                <el-tag v-if="row.deleted_at" size="small" type="info">已删除</el-tag>
              </div>
              <div class="doc-file muted small">
                {{ row.current_version?.file_name ?? '暂无版本' }}
                <span v-if="row.current_version">
                  · {{ formatFileSize(row.current_version.file_size) }}
                </span>
              </div>
              <div v-if="row.tags.length" class="row tag-row">
                <el-tag v-for="tag in row.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="92">
          <template #default="{ row }: { row: DocumentItem }">
            <span class="muted">{{ DOC_TYPE[row.doc_type] ?? row.doc_type }}</span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }: { row: DocumentItem }">
            <el-tag :type="statusMeta(DOCUMENT_STATUS, row.status).tone" size="small">
              {{ statusMeta(DOCUMENT_STATUS, row.status).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="密级" width="88">
          <template #default="{ row }: { row: DocumentItem }">
            <el-tag :type="statusMeta(SENSITIVITY, row.sensitivity).tone" size="small" effect="plain">
              {{ statusMeta(SENSITIVITY, row.sensitivity).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="更新时间" width="150">
          <template #default="{ row }: { row: DocumentItem }">
            <span class="muted small">{{ formatTime(row.updated_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="210" align="right">
          <template #default="{ row }: { row: DocumentItem }">
            <el-button link type="primary" @click="router.push(`/documents/${row.id}`)">详情</el-button>
            <el-button v-if="!row.deleted_at" link @click="download(row)">下载</el-button>
            <el-button v-if="row.deleted_at && auth.has('document:write')" link type="success" @click="restore(row)">
              恢复
            </el-button>
            <el-button
              v-if="!row.deleted_at && auth.has('document:delete')"
              link
              type="danger"
              @click="remove(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty description="还没有文档，先上传一份试试" :image-size="80" />
        </template>
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
  </div>
</template>

<style scoped>
.doc-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.doc-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--ink);
}

.tag-row {
  margin-top: 2px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid var(--line);
}
</style>
