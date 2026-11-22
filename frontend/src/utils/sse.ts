/**
 * SSE 解析：网络分片与事件帧不是一一对应的。
 * 一帧可能跨多个 chunk 到达，一个 chunk 也可能包含多帧，
 * 所以这里保留未完成的尾巴，只解析以空行结尾的完整帧。
 */

export interface SseEvent {
  event: string
  data: string
}

export interface SseParser {
  /** 喂入一段文本，返回本次解析出的完整帧（可能为空）。 */
  push(chunk: string): SseEvent[]
  /** 流结束时调用，把残留内容当成最后一帧处理。 */
  flush(): SseEvent[]
}

const FRAME_SEPARATOR = '\n\n'

function parseFrame(frame: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      data.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }
  if (!data.length) return null
  return { event, data: data.join('\n') }
}

export function createSseParser(): SseParser {
  let buffer = ''

  function drain(final: boolean): SseEvent[] {
    // 统一换行后再按空行切帧，避免 CRLF 跨 chunk 时漏切
    buffer = buffer.replace(/\r\n/g, '\n')
    const parts = buffer.split(FRAME_SEPARATOR)
    buffer = final ? '' : (parts.pop() ?? '')

    const events: SseEvent[] = []
    for (const part of parts) {
      const parsed = parseFrame(part)
      if (parsed) events.push(parsed)
    }
    return events
  }

  return {
    push(chunk: string): SseEvent[] {
      buffer += chunk
      return drain(false)
    },
    flush(): SseEvent[] {
      return drain(true)
    },
  }
}
