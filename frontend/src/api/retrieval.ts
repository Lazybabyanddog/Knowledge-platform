/**
 * 检索与问答。
 *
 * ``search`` 走统一 http 封装（自动带令牌、自动刷新）；
 * ``ask`` 是 SSE 长连接，axios 的响应拦截器与超时都不适用，
 * 因此用原生 fetch + AbortController 手动携带令牌，并自己解包错误信封。
 */
import { createSseParser, type SseEvent } from '@/utils/sse'
import { ApiError, http } from './http'
import type {
  AskDone,
  AskParams,
  Envelope,
  RetrievalSearchParams,
  RetrievalSearchResponse,
  RetrievalSource,
} from './types'

const TOKEN_KEY = 'ai-platform-auth'

export interface AskHandlers {
  onSources?: (sources: RetrievalSource[]) => void
  onDelta?: (text: string) => void
  onDone?: (payload: AskDone) => void
  onError?: (payload: { code: string; message: string }) => void
}

function accessToken(): string {
  const raw = localStorage.getItem(TOKEN_KEY)
  if (!raw) return ''
  try {
    return (JSON.parse(raw) as { accessToken?: string }).accessToken ?? ''
  } catch {
    return ''
  }
}

function dispatch(event: SseEvent, handlers: AskHandlers): void {
  let payload: Record<string, unknown>
  try {
    payload = JSON.parse(event.data) as Record<string, unknown>
  } catch {
    return
  }
  switch (event.event) {
    case 'sources':
      handlers.onSources?.((payload.sources as RetrievalSource[]) ?? [])
      break
    case 'delta':
      handlers.onDelta?.(String(payload.text ?? ''))
      break
    case 'done':
      handlers.onDone?.({
        elapsed_ms: Number(payload.elapsed_ms ?? 0),
        sources: Number(payload.sources ?? 0),
      })
      break
    case 'error':
      handlers.onError?.({
        code: String(payload.code ?? 'UNKNOWN'),
        message: String(payload.message ?? '生成失败'),
      })
      break
    default:
      break
  }
}

async function throwApiError(response: Response): Promise<never> {
  let message = `请求失败（HTTP ${response.status}）`
  let code = 50000
  try {
    const body = (await response.json()) as Envelope<unknown>
    if (typeof body.code === 'number') code = body.code
    if (body.message) message = body.message
  } catch {
    // 非 JSON 响应（网关错误页等）沿用状态码文案
  }
  throw new ApiError(message, code, response.status)
}

export const retrievalApi = {
  search: (params: RetrievalSearchParams) =>
    http.post<RetrievalSearchResponse>('/retrieval/search', params),

  /**
   * 流式问答：按 ``sources -> delta* -> done`` 回调。
   * 调用方通过 ``signal`` 取消；取消时 fetch 会抛 AbortError，由调用方识别。
   */
  async ask(body: AskParams, handlers: AskHandlers, signal?: AbortSignal): Promise<void> {
    const response = await fetch('/api/v1/retrieval/ask', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${accessToken()}`,
      },
      body: JSON.stringify(body),
      signal,
    })

    if (!response.ok) {
      await throwApiError(response)
    }
    if (!response.body) {
      throw new ApiError('当前浏览器不支持流式响应', 50000, 0)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    const parser = createSseParser()
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        for (const event of parser.push(decoder.decode(value, { stream: true }))) {
          dispatch(event, handlers)
        }
      }
      for (const event of parser.flush()) {
        dispatch(event, handlers)
      }
    } finally {
      reader.releaseLock()
    }
  },
}
