<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError, errorMessage } from '@/api/http'
import { qaApi } from '@/api/qa'
import { retrievalApi } from '@/api/retrieval'
import type { DocType, QaDislikeReason, QaFeedback, QaRating, RetrievalSource } from '@/api/types'
import { DOC_TYPE, QA_DISLIKE_REASON } from '@/utils/format'

type Phase = 'idle' | 'streaming' | 'done' | 'stopped' | 'error'
type FeedbackTarget = 'current' | 'last'

/** 只存记录标识，不放答案正文：刷新后回读评价，也避免把资料内容留在本地存储。 */
const LAST_RECORD_KEY = 'ai-platform-last-qa-record'

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

/* ------------------------------- 反馈 ------------------------------- */

const currentRecordId = ref('')
const currentFeedback = ref<QaFeedback | null>(null)
const lastRecordId = ref('')
const lastFeedback = ref<QaFeedback | null>(null)
const feedbackSaving = ref(false)
const feedbackOpen = ref(false)
const feedbackTarget = ref<FeedbackTarget>('current')
const feedbackForm = reactive<{
  rating: QaRating
  reason: QaDislikeReason | ''
  note: string
}>({ rating: 'dislike', reason: '', note: '' })

let controller: AbortController | null = null

const docTypeOptions = Object.entries(DOC_TYPE).map(([value, label]) => ({ value, label }))
const dislikeReasonOptions = Object.entries(QA_DISLIKE_REASON).map(([value, label]) => ({
  value,
  label,
}))
const streaming = computed(() => phase.value === 'streaming')
const canAsk = computed(() => question.value.trim().length > 0 && !streaming.value)
const showEmpty = computed(() => phase.value === 'done' && sources.value.length === 0)
const canGiveFeedback = computed(
  () => phase.value === 'done' && !!currentRecordId.value && !streaming.value,
)
const showLastFeedback = computed(
  () => phase.value === 'idle' && !!lastFeedback.value && !currentRecordId.value,
)
const feedbackNeedsNote = computed(
  () => feedbackForm.rating === 'dislike' && feedbackForm.reason === 'other',
)

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
  currentRecordId.value = ''
  currentFeedback.value = null
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
          currentRecordId.value = payload.qa_record_id
          if (payload.qa_record_id) {
            localStorage.setItem(LAST_RECORD_KEY, payload.qa_record_id)
          }
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

/* ------------------------------- 评价 ------------------------------- */

function feedbackLabel(item: QaFeedback | null): string {
  if (!item) return ''
  if (item.rating === 'like') return '认可'
  if (!item.reason) return '不认可'
  return `不认可 · ${QA_DISLIKE_REASON[item.reason] ?? item.reason}`
}

function targetRecordId(): string {
  return feedbackTarget.value === 'current' ? currentRecordId.value : lastRecordId.value
}

function openFeedbackEditor(target: FeedbackTarget, rating: QaRating): void {
  feedbackTarget.value = target
  const existing = target === 'current' ? currentFeedback.value : lastFeedback.value
  feedbackForm.rating = existing?.rating ?? rating
  feedbackForm.reason = (existing?.reason as QaDislikeReason | null) ?? ''
  feedbackForm.note = existing?.note ?? ''
  feedbackOpen.value = true
}

async function saveFeedback(): Promise<void> {
  const recordId = targetRecordId()
  if (!recordId) return
  if (feedbackForm.rating === 'dislike' && !feedbackForm.reason) {
    ElMessage.warning('请选择不认可的原因')
    return
  }
  if (feedbackNeedsNote.value && !feedbackForm.note.trim()) {
    ElMessage.warning('选择“其他”时请填写补充说明')
    return
  }

  feedbackSaving.value = true
  try {
    const data = await qaApi.submitFeedback(recordId, {
      rating: feedbackForm.rating,
      reason: feedbackForm.rating === 'dislike' ? feedbackForm.reason || null : null,
      note: feedbackForm.rating === 'dislike' ? feedbackForm.note.trim() || null : null,
    })
    if (feedbackTarget.value === 'current') currentFeedback.value = data.feedback
    else lastFeedback.value = data.feedback
    feedbackOpen.value = false
    ElMessage.success('评价已记录')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    feedbackSaving.value = false
  }
}

async function likeNow(target: FeedbackTarget): Promise<void> {
  feedbackTarget.value = target
  feedbackForm.rating = 'like'
  feedbackForm.reason = ''
  feedbackForm.note = ''
  await saveFeedback()
}

function dismissLastFeedback(): void {
  lastFeedback.value = null
  lastRecordId.value = ''
  localStorage.removeItem(LAST_RECORD_KEY)
}

onMounted(async () => {
  const stored = localStorage.getItem(LAST_RECORD_KEY)
  if (!stored) return
  lastRecordId.value = stored
  try {
    lastFeedback.value = (await qaApi.getFeedback(stored)).feedback
  } catch {
    // 记录超期清理或已不属于当前用户：丢掉本地标识，不打扰本次使用
    lastFeedback.value = null
    lastRecordId.value = ''
    localStorage.removeItem(LAST_RECORD_KEY)
    return
  }
  if (!lastFeedback.value) {
    lastRecordId.value = ''
    localStorage.removeItem(LAST_RECORD_KEY)
  }
})

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
      <div v-if="showLastFeedback" class="last-feedback">
        <span class="muted small">上一次回答的评价：</span>
        <el-tag
          size="small"
          :type="lastFeedback?.rating === 'like' ? 'success' : 'danger'"
          effect="light"
        >
          {{ feedbackLabel(lastFeedback) }}
        </el-tag>
        <el-button link type="primary" size="small" @click="openFeedbackEditor('last', 'dislike')">
          修改评价
        </el-button>
        <el-button link size="small" @click="dismissLastFeedback">不再提示</el-button>
      </div>

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

        <div v-if="canGiveFeedback" class="feedback-bar">
          <span class="muted small">这条回答有帮助吗？</span>
          <template v-if="currentFeedback">
            <el-tag
              size="small"
              :type="currentFeedback.rating === 'like' ? 'success' : 'danger'"
              effect="light"
            >
              {{ feedbackLabel(currentFeedback) }}
            </el-tag>
            <el-button
              link
              type="primary"
              size="small"
              @click="openFeedbackEditor('current', 'dislike')"
            >
              修改评价
            </el-button>
          </template>
          <template v-else>
            <el-button size="small" type="success" plain @click="likeNow('current')">
              认可
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              @click="openFeedbackEditor('current', 'dislike')"
            >
              不认可
            </el-button>
          </template>
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

    <el-dialog
      v-model="feedbackOpen"
      :title="feedbackTarget === 'last' ? '评价上一次回答' : '评价这次回答'"
      width="460px"
    >
      <el-form label-position="top">
        <el-form-item label="评价">
          <el-radio-group v-model="feedbackForm.rating">
            <el-radio-button value="like">认可</el-radio-button>
            <el-radio-button value="dislike">不认可</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="feedbackForm.rating === 'dislike'">
          <el-form-item label="原因（必选）">
            <el-select v-model="feedbackForm.reason" placeholder="哪里不满意" style="width: 100%">
              <el-option
                v-for="item in dislikeReasonOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </el-form-item>
          <el-form-item :label="feedbackNeedsNote ? '补充说明（必填）' : '补充说明（可选）'">
            <el-input
              v-model="feedbackForm.note"
              type="textarea"
              :rows="3"
              maxlength="1000"
              show-word-limit
              placeholder="例如：引用的制度版本已经过期"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="feedbackOpen = false">取消</el-button>
        <el-button type="primary" :loading="feedbackSaving" @click="saveFeedback">
          提交评价
        </el-button>
      </template>
    </el-dialog>
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

.last-feedback,
.feedback-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.last-feedback {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--brand-soft);
}

.feedback-bar {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--line);
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
