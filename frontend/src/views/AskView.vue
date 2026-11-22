<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import { ApiError, errorMessage } from '@/api/http'
import { retrievalApi } from '@/api/retrieval'
import type { DocType, RetrievalSource } from '@/api/types'
import { DOC_TYPE } from '@/utils/format'

type Phase = 'idle' | 'streaming' | 'done' | 'stopped' | 'error'

const question = ref('')
const docType = ref<DocType | ''>('')
const topK = ref<number | ''>('')
const answer = ref('')
const sources = ref<RetrievalSource[]>([])
const phase = ref<Phase>('idle')
const errorText = ref('')
const elapsed = ref(0)
const expanded = ref<string[]>([])
const answerBox = ref<HTMLDivElement>()
const stopRequested = ref(false)

let controller: AbortController | null = null

const docTypeOptions = Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))
const streaming = computed(() => phase.value === 'streaming')
const canAsk = computed(() => question.value.trim().length > 0 && !streaming.value)
const showEmpty = computed(() => phase.value === 'done' && sources.value.length === 0)

const statusText: Record<Phase, string> = {
  idle: '待提问',
  streaming: '生成中',
  done: '已完成',
  stopped: '已停止',
  error: '失败',
}

const statusType: Record<Phase, 'info' | 'warning' | 'success' | 'danger'> = {
  idle: 'info',
  streaming: 'warning',
  done: 'success',
  stopped: 'info',
  error: 'danger',
}

function resetAnswer(): void {
  answer.value = ''
  sources.value = []
  errorText.value = ''
  elapsed.value = 0
  expanded.value = []
  stopRequested.value = false
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException ? error.name === 'AbortError' : false
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (answerBox.value) {
    answerBox.value.scrollTop = answerBox.value.scrollHeight
  }
}

async function ask(): Promise<void> {
  const text = question.value.trim()
  if (!text || streaming.value) return

  resetAnswer()
  phase.value = 'streaming'
  controller = new AbortController()

  try {
    await retrievalApi.ask(
      {
        question: text,
        doc_type: docType.value || undefined,
        top_k: topK.value || undefined,
      },
      {
        onSources: (list) => {
          sources.value = list
        },
        onDelta: (chunk) => {
          answer.value += chunk
          void scrollToBottom()
        },
        onDone: (payload) => {
          phase.value = 'done'
          elapsed.value = payload.elapsed_ms
        },
        onError: (payload) => {
          phase.value = 'error'
          errorText.value = payload.message
        },
      },
      controller.signal,
    )
    // 服务端没发 done（例如异常断流）时也要收尾，避免一直停在「生成中」
    if (phase.value === 'streaming') phase.value = 'done'
  } catch (error) {
    if (isAbort(error) || stopRequested.value) {
      phase.value = 'stopped'
      return
    }
    phase.value = 'error'
    errorText.value =
      error instanceof ApiError && error.status === 503
        ? `${error.message}（请检查后端 embedding / LLM 配置）`
        : errorMessage(error)
  } finally {
    controller = null
  }
}

function stop(): void {
  if (!controller) return
  stopRequested.value = true
  controller.abort()
}

function toggleSource(chunkId: string): void {
  expanded.value = expanded.value.includes(chunkId)
    ? expanded.value.filter((item) => item !== chunkId)
    : [...expanded.value, chunkId]
}

function pageLabel(source: RetrievalSource): string {
  if (source.page_range && source.page_range[0] != null) {
    const [start, end] = source.page_range
    return start === end ? `第 ${start} 页` : `第 ${start}-${end} 页`
  }
  return source.page_no != null ? `第 ${source.page_no} 页` : '页码未知'
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    void ask()
  }
}
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">知识问答</h2>
        <p class="page-sub">基于已发布文档的片段召回作答，答案只引用命中的资料并标注编号</p>
      </div>
    </div>

    <section class="card card-pad ask-box">
      <el-input
        v-model="question"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        resize="none"
        placeholder="例如：这份制度里年假如何计算？Ctrl/⌘ + Enter 直接提问"
        @keydown="onKeydown"
      />
      <div class="toolbar">
        <el-select v-model="docType" placeholder="全部类型" clearable class="type-select">
          <el-option
            v-for="item in docTypeOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="topK" placeholder="默认片段数" clearable class="k-select">
          <el-option v-for="k in [3, 5, 8, 10]" :key="k" :label="`${k} 个片段`" :value="k" />
        </el-select>
        <div class="spacer" />
        <el-button v-if="streaming" type="danger" plain @click="stop">停止生成</el-button>
        <el-button type="primary" :disabled="!canAsk" :loading="streaming" @click="ask">
          提问
        </el-button>
      </div>
    </section>

    <section v-if="phase !== 'idle'" class="card answer-card">
      <div class="card-head">
        <div class="row">
          <h3>回答</h3>
          <el-tag size="small" :type="statusType[phase]" effect="light">
            {{ statusText[phase] }}
          </el-tag>
          <span v-if="phase === 'done' && elapsed > 0" class="muted small">
            用时 {{ (elapsed / 1000).toFixed(1) }}s
          </span>
        </div>
        <span class="muted small">命中 {{ sources.length }} 个片段</span>
      </div>

      <div class="card-pad">
        <el-alert
          v-if="phase === 'error'"
          type="error"
          :closable="false"
          show-icon
          :title="errorText || '问答失败'"
        />
        <el-alert
          v-else-if="phase === 'stopped'"
          type="info"
          :closable="false"
          show-icon
          title="已停止生成，下面保留已生成的内容"
        />
        <el-alert
          v-else-if="showEmpty"
          type="warning"
          :closable="false"
          show-icon
          title="知识库未命中相关内容"
          description="试试换一种表述，或确认相关文档是否已通过审批并完成索引"
        />

        <div ref="answerBox" class="answer-body">
          <p v-if="!answer && streaming" class="muted">正在检索并生成…</p>
          <div v-else-if="answer" class="answer-text">
            {{ answer }}<span v-if="streaming" class="caret" />
          </div>
        </div>
      </div>
    </section>

    <section v-if="sources.length" class="card">
      <div class="card-head">
        <h3>引用资料</h3>
        <span class="muted small">编号与答案中的 [n] 对应</span>
      </div>
      <div class="source-list">
        <article v-for="source in sources" :key="source.chunk_id" class="source-item">
          <div class="source-head" @click="toggleSource(source.chunk_id)">
            <span class="badge">[{{ source.index }}]</span>
            <div class="source-title">
              <div class="title-line">{{ source.title }}</div>
              <div class="muted small">
                {{ pageLabel(source) }} · v{{ source.version_no }} ·
                匹配度 {{ (source.score * 100).toFixed(1) }}%
                <template v-if="source.heading_path"> · {{ source.heading_path }}</template>
              </div>
            </div>
            <el-icon class="chev" :class="{ open: expanded.includes(source.chunk_id) }">
              <ArrowDown />
            </el-icon>
          </div>
          <p class="snippet" :class="{ expanded: expanded.includes(source.chunk_id) }">
            {{ source.snippet }}
          </p>
        </article>
      </div>
    </section>

    <section v-else-if="phase === 'idle'" class="card card-pad hint">
      <h3>使用说明</h3>
      <ul>
        <li>只会召回已发布、未下架且在当前空间内的文档片段；密级超出可见范围的资料不会出现。</li>
        <li>答案仅依据命中片段生成，资料不足时会直接说明「未找到相关内容」。</li>
        <li>检索依赖向量索引：新审批通过的文档需要等待索引完成后再提问。</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.ask-box {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.type-select {
  width: 132px;
}

.k-select {
  width: 132px;
}

.answer-card .answer-body {
  margin-top: 12px;
}

.answer-body {
  max-height: 420px;
  overflow-y: auto;
}

.answer-text {
  white-space: pre-wrap;
  line-height: 1.75;
  font-size: 14.5px;
  color: var(--ink);
}

.caret {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--brand);
  animation: blink 1s steps(2, start) infinite;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.source-list {
  display: flex;
  flex-direction: column;
}

.source-item {
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
}

.source-item:last-child {
  border-bottom: none;
}

.source-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
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

.source-title {
  flex: 1;
  min-width: 0;
}

.title-line {
  font-weight: 600;
  font-size: 13.5px;
}

.chev {
  color: var(--muted);
  transition: transform 0.18s ease;
}

.chev.open {
  transform: rotate(180deg);
}

.snippet {
  margin: 8px 0 0 34px;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.snippet.expanded {
  display: block;
  -webkit-line-clamp: unset;
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
