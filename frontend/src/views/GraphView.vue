<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'

import { errorMessage } from '@/api/http'
import { knowledgeApi } from '@/api/knowledge'
import type { EntityItem, Subgraph } from '@/api/types'
import { ENTITY_STATUS, classLabel, statusMeta } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const keyword = ref('')
const searching = ref(false)
const candidates = ref<EntityItem[]>([])
const loading = ref(false)
const subgraph = ref<Subgraph | null>(null)
const active = ref<EntityItem | null>(null)
const params = reactive({ depth: 2, include_proposed: true })

const chartRef = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

const PALETTE: Record<string, string> = {
  confirmed: '#4f46e5',
  proposed: '#f59e0b',
  merged: '#98a2b3',
  rejected: '#ef4444',
}

function nodeColor(status: string): string {
  return PALETTE[status] ?? '#4f46e5'
}

async function search(): Promise<void> {
  if (!keyword.value.trim()) {
    ElMessage.warning('请输入实体名称')
    return
  }
  searching.value = true
  try {
    const data = await knowledgeApi.entities({
      keyword: keyword.value.trim(),
      page: 1,
      page_size: 10,
    })
    candidates.value = data.items.filter((item) => item.status !== 'merged')
    if (!candidates.value.length) ElMessage.info('没有匹配的实体')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    searching.value = false
  }
}

async function loadSubgraph(entityId: string): Promise<void> {
  loading.value = true
  try {
    const data = await knowledgeApi.subgraph(entityId, {
      depth: params.depth,
      include_proposed: params.include_proposed,
    })
    subgraph.value = data
    active.value = data.nodes.find((item) => item.id === entityId) ?? null
    await nextTick()
    render()
    await router.replace({ query: { entity: entityId, depth: String(params.depth) } })
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function render(): void {
  if (!chartRef.value || !subgraph.value) return
  if (!chart) chart = echarts.init(chartRef.value)

  const data = subgraph.value.nodes
  const degree: Record<string, number> = {}
  for (const edge of subgraph.value.edges) {
    degree[edge.source_id] = (degree[edge.source_id] ?? 0) + 1
    degree[edge.target_id] = (degree[edge.target_id] ?? 0) + 1
  }

  chart.setOption(
    {
      backgroundColor: 'transparent',
      tooltip: {
        backgroundColor: '#111827',
        borderWidth: 0,
        textStyle: { color: '#f9fafb', fontSize: 12 },
        formatter: (params: any) => {
          if (params.dataType === 'edge') {
            return `${params.data.sourceName} → ${params.data.targetName}<br/>${params.data.code}`
          }
          const meta = statusMeta(ENTITY_STATUS, params.data.status)
          return `<b>${params.data.name}</b><br/>${params.data.classLabel}<br/>${meta.label} · 置信度 ${Math.round(
            (params.data.confidence ?? 0) * 100,
          )}%`
        },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          symbolSize: 18,
          label: { show: true, fontSize: 11, color: '#344054', position: 'right' },
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 7,
          force: { repulsion: 180, edgeLength: [70, 140], gravity: 0.06 },
          itemStyle: { borderWidth: 2, borderColor: '#fff' },
          lineStyle: { color: '#c9cde0', width: 1.3, curveness: 0.08, opacity: 0.9 },
          emphasis: { focus: 'adjacency', lineStyle: { width: 2.4, opacity: 1 } },
          data: data.map((node) => ({
            id: node.id,
            name: node.name,
            classLabel: classLabel(node.class_code),
            status: node.status,
            confidence: node.confidence,
            symbolSize: 18 + Math.min(degree[node.id] ?? 0, 8) * 3,
            itemStyle: { color: nodeColor(node.status) },
          })),
          links: subgraph.value.edges.map((edge) => ({
            source: edge.source_id,
            target: edge.target_id,
            code: edge.code,
            sourceName: data.find((item) => item.id === edge.source_id)?.name ?? '',
            targetName: data.find((item) => item.id === edge.target_id)?.name ?? '',
            lineStyle: edge.status === 'proposed' ? { type: 'dashed', opacity: 0.7 } : {},
          })),
        },
      ],
    },
    true,
  )
}

function resize(): void {
  chart?.resize()
}

function focusNode(entityId: string): void {
  void loadSubgraph(entityId)
}

function refresh(): void {
  if (active.value) void loadSubgraph(active.value.id)
}

onMounted(async () => {
  window.addEventListener('resize', resize)
  const entityId = typeof route.query.entity === 'string' ? route.query.entity : ''
  if (entityId) {
    const depth = Number(route.query.depth)
    if (depth >= 1 && depth <= 3) params.depth = depth
    try {
      const entity = await knowledgeApi.entity(entityId)
      candidates.value = [entity]
      keyword.value = entity.name
    } catch {
      // 实体可能已删除，忽略
    }
    await loadSubgraph(entityId)
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1 class="page-title">图谱浏览</h1>
        <p class="page-sub">以实体为中心展开 1–3 跳邻居；虚线为待审核关系。</p>
      </div>
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="搜索实体名称"
          clearable
          style="width: 220px"
          @keyup.enter="search"
        >
          <template #append>
            <el-button :loading="searching" @click="search">搜索</el-button>
          </template>
        </el-input>
        <el-select v-model="params.depth" style="width: 110px" @change="refresh">
          <el-option label="1 跳" :value="1" />
          <el-option label="2 跳" :value="2" />
          <el-option label="3 跳" :value="3" />
        </el-select>
        <el-checkbox v-model="params.include_proposed" @change="refresh">含待审核</el-checkbox>
        <el-button :disabled="!active" @click="refresh">刷新</el-button>
      </div>
    </div>

    <div v-if="candidates.length" class="row candidate-row">
      <span class="muted small">匹配：</span>
      <el-tag
        v-for="item in candidates"
        :key="item.id"
        :effect="active?.id === item.id ? 'dark' : 'plain'"
        class="candidate"
        @click="focusNode(item.id)"
      >
        {{ item.name }}
        <span class="muted small">· {{ classLabel(item.class_code) }}</span>
      </el-tag>
    </div>

    <div class="graph-grid">
      <div v-loading="loading" class="card graph-card">
        <div v-if="!subgraph" class="graph-empty">
          <el-icon class="empty-icon"><Connection /></el-icon>
          <p>搜索并选择一个实体，查看它周围的子图</p>
        </div>
        <div ref="chartRef" class="chart" :class="{ hidden: !subgraph }" />
        <div v-if="subgraph" class="graph-foot">
          <span class="muted small">
            {{ subgraph.nodes.length }} 个节点 · {{ subgraph.edges.length }} 条关系 · 深度 {{ subgraph.depth }}
          </span>
          <el-tag v-if="subgraph.truncated" size="small" type="warning">已达到 200 节点上限</el-tag>
        </div>
      </div>

      <div class="card side">
        <div class="card-head">
          <h3>节点详情</h3>
        </div>
        <div class="card-pad">
          <template v-if="active">
            <div class="stack">
              <div class="entity-title">{{ active.name }}</div>
              <div class="row">
                <el-tag size="small" effect="plain">{{ classLabel(active.class_code) }}</el-tag>
                <el-tag :type="statusMeta(ENTITY_STATUS, active.status).tone" size="small">
                  {{ statusMeta(ENTITY_STATUS, active.status).label }}
                </el-tag>
              </div>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="置信度">
                  {{ Math.round(active.confidence * 100) }}%
                </el-descriptions-item>
                <el-descriptions-item label="别名">
                  {{ active.aliases.length ? active.aliases.join('、') : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="证据块">
                  {{ active.evidence_block_ids.length }}
                </el-descriptions-item>
                <el-descriptions-item label="描述">
                  {{ active.description ?? '—' }}
                </el-descriptions-item>
              </el-descriptions>
              <div class="row">
                <el-button size="small" type="primary" plain @click="router.push(`/review?keyword=${active.name}`)">
                  去审核
                </el-button>
                <el-button size="small" @click="router.push(`/documents/${active.document_id ?? ''}`)" :disabled="!active.document_id">
                  打开文档
                </el-button>
              </div>
            </div>
          </template>
          <div v-else class="muted small">点击图中任意节点查看详情</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.candidate-row {
  margin-top: -4px;
}

.candidate {
  cursor: pointer;
}

.graph-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 16px;
  align-items: start;
}

.graph-card {
  position: relative;
  min-height: 560px;
  display: flex;
  flex-direction: column;
}

.chart {
  flex: 1;
  min-height: 520px;
}

.chart.hidden {
  display: none;
}

.graph-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

.empty-icon {
  font-size: 40px;
  color: #d0d5dd;
}

.graph-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-top: 1px solid var(--line);
}

.entity-title {
  font-size: 16px;
  font-weight: 650;
}

@media (max-width: 1200px) {
  .graph-grid {
    grid-template-columns: 1fr;
  }
}
</style>
