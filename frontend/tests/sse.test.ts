/**
 * SSE 解析单测：用 Node 内置 test runner（node --test）跑，不依赖浏览器。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { createSseParser } from '../src/utils/sse.ts'

test('一帧跨多个 chunk 到达时仍能正确拼出', () => {
  const parser = createSseParser()

  assert.deepEqual(parser.push('event: delta\ndata: {"text":"你'), [])
  assert.deepEqual(parser.push('好"}\n\n'), [{ event: 'delta', data: '{"text":"你好"}' }])
})

test('一个 chunk 含多个事件时全部产出且保持顺序', () => {
  const parser = createSseParser()

  const events = parser.push(
    'event: sources\ndata: {"sources":[]}\n\n' +
      'event: delta\ndata: {"text":"根据"}\n\n' +
      'event: done\ndata: {"elapsed_ms":12,"sources":0}\n\n',
  )

  assert.deepEqual(
    events.map((item) => item.event),
    ['sources', 'delta', 'done'],
  )
})

test('CRLF 被拆到两个 chunk 时不会漏切帧', () => {
  const parser = createSseParser()

  assert.deepEqual(parser.push('event: delta\r\ndata: {"text":"a"}\r'), [])
  const events = parser.push('\n\r\n')
  assert.deepEqual(events, [{ event: 'delta', data: '{"text":"a"}' }])
})

test('忽略注释行与空帧，多行 data 用换行拼接', () => {
  const parser = createSseParser()

  const events = parser.push(': keep-alive\n\n' + 'event: delta\ndata: 第一行\ndata: 第二行\n\n')

  assert.deepEqual(events, [{ event: 'delta', data: '第一行\n第二行' }])
})

test('流结束时 flush 残留帧', () => {
  const parser = createSseParser()

  assert.deepEqual(parser.push('event: error\ndata: {"code":"X"}'), [])
  assert.deepEqual(parser.flush(), [{ event: 'error', data: '{"code":"X"}' }])
})
