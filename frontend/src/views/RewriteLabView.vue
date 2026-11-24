<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { ApiError, errorMessage } from '@/api/http'
import { queryRewriteApi } from '@/api/queryRewrite'
import type {
  DocType,
  RewriteHistoryTurn,
  RewriteRunParams,
  RewriteTrace,
} from '@/api/types'
import { DOC_TYPE, formatDuration, formatPercent } from '@/utils/format'
import {
  DEFAULT_ALLOWLIST,
  LAB_STRATEGIES,
  armView,
  comparisonRows,
  decisionMeta,
  liftText,
  liftTone,
  rewriteAlerts,
  scoreText,
  stepStatusMeta,
} from '@/utils/rewrite'

interface HistoryDraft {
  role: 'user' | 'assistant'
  content: string
}

const form = reactive({
  query: '',
  mode: 'auto' as 'auto' | 'manual',
  allowlist: [...DEFAULT_ALLOWLIST],
  budget: 3,
  qualityThreshold: 0.6,
  topK: '' as number | '',
  docType: '' as DocType | '',
  history: [] as HistoryDraft[],
})

const running = ref(false)
const trace = ref<RewriteTrace | null>(null)
const errorText = ref('')
const expanded = ref<string[]>([])

const docTypeOptions = Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))
const enabledStrategies = LAB_STRATEGIES.filter((item) => item.enabled)
const disabledStrategies = LAB_STRATEGIES.filter((item) => !item.enabled)
const canRun = computed(() => form.query.trim().length > 0 && !running.value)

const alerts = computed(() => (trace.value ? rewriteAlerts(trace.value) : []))
const rows = computed(() => (trace.value ? comparisonRows(trace.value) : []))
const finalQuery = computed(() => {
  if (!trace.value) return ''
  const arm = armView(trace.value, 'rewritten')
  return arm.executed ? arm.queries[0] : trace.value.request.query
})
const unavailable = computed(
  () => trace.value?.routing.filter((item) => item.decision === 'unavailable') ?? [],
)
const rewrittenArm = computed(() => (trace.value ? armView(trace.value, 'rewritten') : null))
const rewrittenQueries = computed(() => (trace.value ? armView(trace.value, 'rewritten').queries : []))

function addTurn(): void {
  if (form.history.length >= 6) return
  form.history.push({ role: form.history.length % 2 === 0 ? 'user' : 'assistant', content: '' })
}

function removeTurn(index: number): void {
  form.history.splice(index, 1)
}

function clearHistory(): void {
  form.history = []
}

function toggleExpanded(chunkId: string): void {
  expanded.value = expanded.value.includes(chunkId)
    ? expanded.value.filter((item) => item !== chunkId)
    : [...expanded.value, chunkId]
}

function alertType(level: 'info' | 'warning' | 'danger'): 'info' | 'warning' | 'error' {
  return level === 'danger' ? 'error' : level
}

function trendClass(trend: 'up' | 'down' | 'flat'): string {
  return `trend-${trend}`
}

async function run(): Promise<void> {
  const query = form.query.trim()
  if (!query || running.value) return

  running.value = true
  errorText.value = ''
  // 旧结果先清掉：错误态不能和上一次成功的结果混排
  trace.value = null
  expanded.value = []

  const history: RewriteHistoryTurn[] = form.history
    .map((turn) => ({ role: turn.role, content: turn.content.trim() }))
    .filter((turn) => turn.content.length > 0)

  const payload: RewriteRunParams = {
    query,
    mode: form.mode,
    allowlist: [...form.allowlist],
    budget: form.budget,
    quality_threshold: form.qualityThreshold,
    top_k: form.topK === '' ? undefined : Number(form.topK),
    doc_type: form.docType === '' ? undefined : form.docType,
  }
  if (history.length > 0) payload.history = history

  try {
    trace.value = await queryRewriteApi.run(payload)
  } catch (error) {
    errorText.value =
      error instanceof ApiError && error.status === 503
        ? `${error.message}（请检查后端 embedding / LLM 配置）`
        : errorMessage(error)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">改写实验室</h2>
        <p class="page-sub">
          同一个问题，改配置就能看出改写策略有没有把召回变好。运行只读：不写文档、索引与审批数据。
        </p>
      </div>
    </div>

    <section class="card card-pad config">
      <el-input
        v-model="form.query"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        resize="none"
        placeholder="例如：年假怎么算？"
      />

      <div class="toolbar">
        <el-radio-group v-model="form.mode" size="small">
          <el-radio-button value="auto">自动路由</el-radio-button>
          <el-radio-button value="manual">手动指定</el-radio-button>
        </el-radio-group>
        <el-select v-model="form.docType" placeholder="全部类型" clearable class="w-130">
          <el-option
            v-for="item in docTypeOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="form.topK" placeholder="默认片段数" clearable class="w-130">
          <el-option v-for="k in [3, 5, 8, 10, 15]" :key="k" :label="`${k} 个片段`" :value="k" />
        </el-select>
        <span class="muted small">策略预算</span>
        <el-input-number v-model="form.budget" :min="1" :max="7" size="small" />
        <div class="spacer" />
        <el-button type="primary" :disabled="!canRun" :loading="running" @click="run">
          运行
        </el-button>
      </div>

      <div class="toolbar">
        <span class="muted small">质量阈值</span>
        <el-slider
          v-model="form.qualityThreshold"
          :min="0"
          :max="1"
          :step="0.05"
          :format-tooltip="(value: number) => formatPercent(value)"
          class="threshold"
        />
        <span class="mono">{{ formatPercent(form.qualityThreshold) }}</span>
      </div>

      <div class="stack">
        <div class="row">
          <span class="label">策略允许列表</span>
          <span class="muted small">勾选表示"允许使用"；是否真正执行仍由规则特征、依赖与预算决定。</span>
        </div>
        <el-checkbox-group v-model="form.allowlist" size="small">
          <el-checkbox v-for="item in enabledStrategies" :key="item.code" :value="item.code">
            {{ item.label }}
          </el-checkbox>
        </el-checkbox-group>
        <div class="row">
          <el-tooltip v-for="item in disabledStrategies" :key="item.code" :content="item.hint">
            <el-checkbox :model-value="false" disabled size="small">
              {{ item.label }}（未启用）
            </el-checkbox>
          </el-tooltip>
        </div>
      </div>

      <div class="stack">
        <div class="row">
          <span class="label">对话历史</span>
          <span class="muted small">含"它 / 那个"等指代时才有意义；不填则不解析指代。</span>
          <div class="spacer" />
          <el-button size="small" text @click="addTurn">添加一轮</el-button>
          <el-button size="small" text :disabled="!form.history.length" @click="clearHistory">
            清空
          </el-button>
        </div>
        <div v-for="(turn, index) in form.history" :key="index" class="history-row">
          <el-select v-model="turn.role" size="small" class="w-100">
            <el-option label="用户" value="user" />
            <el-option label="助手" value="assistant" />
          </el-select>
          <el-input v-model="turn.content" size="small" maxlength="2000" placeholder="历史内容" />
          <el-button size="small" text type="danger" @click="removeTurn(index)">删除</el-button>
        </div>
      </div>
    </section>

    <el-alert
      v-if="errorText"
      type="error"
      :closable="false"
      show-icon
      :title="errorText"
      description="检查查询与配置后重新运行；上一次成功的结果已被清除。"
    />

    <template v-if="trace">
      <el-alert
        v-for="(alert, index) in alerts"
        :key="`${alert.title}-${index}`"
        :type="alertType(alert.level)"
        :closable="false"
        show-icon
        :title="alert.title"
        :description="alert.detail || undefined"
      />

      <section class="card card-pad echo">
        <div class="row">
          <el-tag size="small" effect="plain">
            {{ trace.request.mode === 'auto' ? '自动路由' : '手动指定' }}
          </el-tag>
          <el-tag v-if="trace.degraded" size="small" type="warning" effect="light">降级运行</el-tag>
          <span class="muted small">
            允许列表：{{ trace.request.allowlist.join(' / ') || '（空）' }}
          </span>
          <span class="muted small">
            预算 {{ trace.request.budget }} · 阈值 {{ formatPercent(trace.request.quality_threshold) }} ·
            top_k {{ trace.request.top_k }} · 历史 {{ trace.request.history_turns }} 轮
          </span>
        </div>
        <p class="muted small">以上是本次运行实际生效的配置，不随表单变化。</p>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>1 · 查询理解</h3>
          <el-tag size="small" :type="trace.understanding.degraded ? 'warning' : 'info'" effect="light">
            {{ trace.understanding.source }}
          </el-tag>
        </div>
        <div class="card-pad">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="意图">{{ trace.understanding.intent || '—' }}</el-descriptions-item>
            <el-descriptions-item label="复杂度">
              {{ trace.understanding.complexity || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="实体">
              <template v-if="trace.understanding.entities.length">
                <el-tag
                  v-for="entity in trace.understanding.entities"
                  :key="entity"
                  size="small"
                  effect="plain"
                  class="tag-gap"
                >
                  {{ entity }}
                </el-tag>
              </template>
              <span v-else class="muted">—</span>
            </el-descriptions-item>
            <el-descriptions-item label="模型候选策略">
              {{ trace.understanding.candidate_strategies.join(' / ') || '—（仅作证据，不触发执行）' }}
            </el-descriptions-item>
            <el-descriptions-item label="指代">
              <template v-if="trace.understanding.references.length">
                <div v-for="ref in trace.understanding.references" :key="ref.cue" class="row">
                  <span class="mono">{{ ref.cue }}</span>
                  <el-tag size="small" :type="ref.resolved ? 'success' : 'warning'" effect="plain">
                    {{ ref.resolved ? '已解析' : '未解析' }}
                  </el-tag>
                  <span class="muted small">{{ ref.reason }}</span>
                </div>
              </template>
              <span v-else class="muted">无指代</span>
            </el-descriptions-item>
            <el-descriptions-item label="约束">
              {{ trace.understanding.constraints.join('；') || '—' }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>2 · 路由决策</h3>
          <span class="muted small">共 {{ trace.routing.length }} 个策略，每个策略恰好一条决策</span>
        </div>
        <el-table :data="trace.routing" size="small">
          <el-table-column label="策略" width="170">
            <template #default="{ row }">
              <div>{{ row.label }}</div>
              <div class="muted small mono">{{ row.strategy }}</div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="80">
            <template #default="{ row }">
              {{ row.kind === 'text' ? '改写主查询' : '扩展查询' }}
            </template>
          </el-table-column>
          <el-table-column label="决策" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="decisionMeta(row.decision).tone" effect="light">
                {{ decisionMeta(row.decision).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="依据" min-width="220">
            <template #default="{ row }">
              <template v-if="row.evidence.length">
                <el-tag
                  v-for="item in row.evidence"
                  :key="item"
                  size="small"
                  effect="plain"
                  class="tag-gap"
                >
                  {{ item }}
                </el-tag>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="理由" min-width="200">
            <template #default="{ row }">
              <span class="muted small">{{ row.reason || '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>3 · 工作流执行</h3>
          <span class="muted small">只显示本次实际经过的节点</span>
        </div>
        <div class="card-pad">
          <div class="flow">
            <template v-for="(node, index) in trace.workflow" :key="node.id">
              <div class="flow-node" :class="`node-${node.status}`">
                <div class="flow-label">{{ node.label }}</div>
                <div class="muted small">
                  {{ node.kind }} · {{ node.status }}
                  <template v-if="node.elapsed_ms > 0"> · {{ node.elapsed_ms }} ms</template>
                </div>
                <div v-if="node.depends_on.length" class="muted small">
                  依赖 {{ node.depends_on.join('、') }}
                </div>
              </div>
              <span v-if="index < trace.workflow.length - 1" class="flow-arrow">→</span>
            </template>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>4 · 改写前后</h3>
          <span class="muted small">主查询按执行顺序串联；扩展类策略只追加检索查询</span>
        </div>
        <div class="card-pad stack">
          <div class="row">
            <span class="muted small">最终主查询</span>
            <span class="mono">{{ finalQuery }}</span>
          </div>

          <article v-for="step in trace.rewrites" :key="step.strategy" class="rewrite-item">
            <div class="row">
              <strong>{{ step.label }}</strong>
              <el-tag size="small" :type="stepStatusMeta(step.status).tone" effect="light">
                {{ stepStatusMeta(step.status).label }}
              </el-tag>
              <span class="muted small">{{ step.reason }}</span>
            </div>
            <div class="diff">
              <div class="diff-cell">
                <div class="muted small">改写前</div>
                <p>{{ step.text_in }}</p>
              </div>
              <span class="flow-arrow">→</span>
              <div class="diff-cell">
                <div class="muted small">改写后</div>
                <p>{{ step.text_out || '（无变化）' }}</p>
              </div>
            </div>
          </article>

          <p v-if="!trace.rewrites.length" class="muted">本次没有任何策略产出改写。</p>

          <div v-if="unavailable.length" class="disabled-block">
            <div class="muted small">已登记但本版本不启用的策略：不产生改写文本，也不产生检索结果</div>
            <div class="row">
              <el-tooltip
                v-for="item in unavailable"
                :key="item.strategy"
                :content="item.reason || '当前版本未启用'"
              >
                <el-tag type="info" effect="plain">{{ item.label }} · 未启用</el-tag>
              </el-tooltip>
            </div>
          </div>

          <div v-if="rewrittenQueries.length" class="row">
            <span class="muted small">改写组检索查询</span>
            <el-tag v-for="query in rewrittenQueries" :key="query" size="small" effect="plain">
              {{ query }}
            </el-tag>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>5 · 双路指标与增益</h3>
          <el-tag
            size="small"
            :type="trace.evaluation.verdict === 'sufficient' ? 'success' : 'warning'"
            effect="light"
          >
            {{ trace.evaluation.verdict === 'sufficient' ? '召回充分' : '召回不足（低置信度）' }}
          </el-tag>
        </div>
        <div class="card-pad stack">
          <el-alert
            v-if="rewrittenArm && !rewrittenArm.executed"
            type="warning"
            :closable="false"
            show-icon
            title="改写组未执行"
            :description="`${rewrittenArm.reason}；下表改写组指标显示为未完成，不用 0 冒充结果。`"
          />

          <el-table :data="rows" size="small">
            <el-table-column prop="label" label="指标" width="150" />
            <el-table-column prop="baseline" label="对照组（原始查询）" />
            <el-table-column prop="rewritten" label="改写组" />
            <el-table-column label="差值" width="130">
              <template #default="{ row }">
                <span :class="trendClass(row.trend)">{{ row.delta }}</span>
              </template>
            </el-table-column>
          </el-table>

          <div class="row">
            <span class="muted small">最高分增益</span>
            <el-tag
              v-if="rewrittenArm?.executed"
              :type="liftTone(trace.evaluation.lift)"
              effect="light"
              size="small"
            >
              {{ liftText(trace.evaluation.lift) }}
            </el-tag>
            <el-tag v-else type="info" effect="light" size="small">未完成（只有对照组）</el-tag>
            <span class="muted small">{{ trace.evaluation.reason }}</span>
          </div>
          <div class="row">
            <span class="muted small">阈值 {{ formatPercent(trace.request.quality_threshold) }}</span>
            <span class="muted small">
              对照组 {{ scoreText(armView(trace, 'baseline').top1) }} · 改写组
              {{ rewrittenArm?.executed ? scoreText(rewrittenArm.top1) : '未完成' }}
            </span>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>6 · 二阶段补充</h3>
          <el-tag size="small" :type="trace.second_stage.triggered ? 'success' : 'info'" effect="light">
            {{ trace.second_stage.triggered ? '已触发' : '未触发' }}
          </el-tag>
        </div>
        <div class="card-pad">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="动作">
              {{ trace.second_stage.actions.join(' / ') || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="新增片段">
              {{ trace.second_stage.new_chunks }}
            </el-descriptions-item>
            <el-descriptions-item label="说明" :span="2">
              {{ trace.second_stage.reason || '首轮已满足阈值，未做补充检索' }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </section>

      <section class="card">
        <div class="card-head">
          <h3>7 · 融合结果</h3>
          <span class="muted small">
            {{ trace.fusion.method.toUpperCase() }}(k={{ trace.fusion.k }}) ·
            {{ trace.fusion.before }} → {{ trace.fusion.after }} 条
          </span>
        </div>
        <div class="fused-list">
          <article v-for="item in trace.fusion.items" :key="item.chunk_id" class="fused-item">
            <div class="row">
              <span class="badge">{{ item.index }}</span>
              <strong>{{ item.title }}</strong>
              <span class="muted small">
                v{{ item.version_no }} · 相似度 {{ scoreText(item.score) }}
                <template v-if="item.heading_path"> · {{ item.heading_path }}</template>
              </span>
              <div class="spacer" />
              <el-tag
                v-for="source in item.sources"
                :key="source"
                size="small"
                effect="plain"
                class="tag-gap"
              >
                {{ source === 'baseline' ? '对照组' : '改写组' }}
              </el-tag>
            </div>
            <p class="fused-content" :class="{ expanded: expanded.includes(item.chunk_id) }">
              {{ item.content }}
            </p>
            <el-button
              v-if="item.truncated"
              size="small"
              text
              type="primary"
              @click="toggleExpanded(item.chunk_id)"
            >
              {{ expanded.includes(item.chunk_id) ? '收起' : '展开（内容已截断至 600 字）' }}
            </el-button>
          </article>
          <p v-if="!trace.fusion.items.length" class="muted">没有命中任何片段。</p>
        </div>
      </section>

      <section class="card card-pad totals">
        <div class="stat">
          <div class="label">模型调用</div>
          <div class="value">{{ trace.totals.llm_calls }}</div>
        </div>
        <div class="stat">
          <div class="label">检索查询</div>
          <div class="value">{{ trace.totals.retrieval_calls }}</div>
        </div>
        <div class="stat">
          <div class="label">嵌入调用</div>
          <div class="value">{{ trace.totals.embed_calls }}</div>
        </div>
        <div class="stat">
          <div class="label">总耗时</div>
          <div class="value">{{ formatDuration(trace.totals.elapsed_ms) }}</div>
        </div>
      </section>
    </template>

    <section v-else-if="!running" class="card card-pad hint">
      <h3>怎么用</h3>
      <ul>
        <li>保持默认的五个策略，先用一条真实问题跑一次，看决策表里"依据"是否和你的直觉一致。</li>
        <li>改完配置再跑一次：预算会按优先级截断执行集，允许列表只表示"可以用"，不是"必须用"。</li>
        <li>看改写是否有效只认对照表：最高分增益为负就说明改写把召回改差了。</li>
        <li>HyDE 与 Step-back 只登记未启用，勾不上是刻意的，不要从结果里找它们的改写文本。</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.config {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.w-100 {
  width: 100px;
  flex: 0 0 auto;
}

.w-130 {
  width: 130px;
}

.threshold {
  flex: 1;
  min-width: 180px;
  max-width: 320px;
}

.label {
  font-size: 13px;
  font-weight: 600;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.echo p {
  margin: 8px 0 0;
}

.tag-gap {
  margin-right: 6px;
}

.flow {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  gap: 8px;
}

.flow-node {
  min-width: 130px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fafbfc;
}

.flow-node.node-ok {
  border-color: #b7e4c7;
  background: #f3fbf6;
}

.flow-node.node-degraded {
  border-color: #f3d19e;
  background: #fffaf0;
}

.flow-node.node-skipped {
  border-style: dashed;
}

.flow-label {
  font-size: 13px;
  font-weight: 600;
}

.flow-arrow {
  align-self: center;
  color: var(--muted);
}

.rewrite-item {
  padding: 12px 0;
  border-bottom: 1px dashed var(--line);
}

.rewrite-item:last-of-type {
  border-bottom: none;
}

.diff {
  display: flex;
  gap: 10px;
  margin-top: 8px;
  align-items: stretch;
}

.diff-cell {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fafbfc;
  border: 1px solid var(--line);
}

.diff-cell p {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
}

.disabled-block {
  padding: 10px 12px;
  border: 1px dashed var(--line);
  border-radius: 10px;
}

.trend-up {
  color: #12b76a;
  font-weight: 600;
}

.trend-down {
  color: #d92d20;
  font-weight: 600;
}

.trend-flat {
  color: var(--muted);
}

.fused-list {
  display: flex;
  flex-direction: column;
}

.fused-item {
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
}

.fused-item:last-child {
  border-bottom: none;
}

.badge {
  flex: 0 0 auto;
  padding: 1px 7px;
  border-radius: 6px;
  background: var(--brand-soft);
  color: var(--brand);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.fused-content {
  margin: 8px 0 0;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.fused-content.expanded {
  display: block;
  -webkit-line-clamp: unset;
}

.totals {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 12px;
}

.totals .label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

.totals .value {
  margin-top: 4px;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.4px;
}

.hint h3 {
  margin: 0 0 8px;
  font-size: 15px;
}

.hint ul {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.9;
}
</style>
