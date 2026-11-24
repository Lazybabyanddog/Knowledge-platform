/**
 * 权限配置单测：前端只保留编码到中文名的字典，不再维护"角色 → 权限"矩阵。
 * 真正的映射由后端数据库下发（/auth/me 的 permissions）。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { PERMISSION_GROUPS, ROLE_LABELS, permissionLabel } from '../src/constants.ts'

const expectCodes = [
  'user:manage',
  'department:manage',
  'role:manage',
  'audit:read',
  'document:read',
  'document:write',
  'document:delete',
  'parse:run',
  'ontology:read',
  'ontology:manage',
  'ontology:review',
  'graph:read',
  'review:submit',
  'review:approve',
]

test('权限目录覆盖后端全部权限编码', () => {
  const codes = PERMISSION_GROUPS.flatMap((group) => group.items.map((item) => item.code))
  assert.deepEqual([...codes].sort(), [...expectCodes].sort())
})

test('未知权限编码原样返回，避免界面出现空白', () => {
  assert.equal(permissionLabel('document:read'), '文档查看')
  assert.equal(permissionLabel('brand:new'), 'brand:new')
})

test('角色字典只提供中文名，不含权限信息', () => {
  for (const [code, label] of Object.entries(ROLE_LABELS)) {
    assert.equal(typeof code, 'string')
    assert.equal(typeof label, 'string')
  }
})
