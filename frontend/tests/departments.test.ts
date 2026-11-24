/** 部门树摊平：下拉选项顺序、层级缩进与排序。 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { DepartmentItem } from '../src/api/types.ts'
import { flattenDepartments } from '../src/utils/departments.ts'

function dept(
  id: string,
  name: string,
  parent_id: string | null = null,
  sort_order = 0,
): DepartmentItem {
  return { id, name, parent_id, sort_order, user_count: 0 }
}

test('按层级摊平并保留缩进', () => {
  const options = flattenDepartments([
    dept('c', '平台组', 'b'),
    dept('a', '技术中心', null, 1),
    dept('b', '研发部', 'a', 1),
    dept('d', '财务部', null, 0),
  ])

  assert.deepEqual(
    options.map((item) => [item.value, item.depth]),
    [
      ['d', 0],
      ['a', 0],
      ['b', 1],
      ['c', 2],
    ],
  )
  assert.equal(options[2].label, '　研发部')
})

test('同级按 sort_order 再按名称排序', () => {
  const options = flattenDepartments([dept('b', '乙', null, 2), dept('a', '甲', null, 1)])
  assert.deepEqual(
    options.map((item) => item.name),
    ['甲', '乙'],
  )
})

test('孤儿部门（父节点缺失）仍可展示为根节点', () => {
  const options = flattenDepartments([dept('x', '游离部门', 'missing')])
  assert.equal(options.length, 1)
  assert.equal(options[0].depth, 0)
})
