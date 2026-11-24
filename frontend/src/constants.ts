/**
 * 前端只保留权限编码与中文标签，用于渲染与菜单守卫。
 * 角色 → 权限的映射由后端数据库下发（/auth/me 的 permissions、/roles 的 permissions），
 * 前端不再维护权限矩阵，避免新增权限时两处不同步。
 */

export type Permission =
  | 'user:manage'
  | 'department:manage'
  | 'role:manage'
  | 'audit:read'
  | 'document:read'
  | 'document:write'
  | 'document:delete'
  | 'parse:run'
  | 'ontology:read'
  | 'ontology:manage'
  | 'ontology:review'
  | 'graph:read'
  | 'review:submit'
  | 'review:approve'
  | 'qa:manage'

export interface PermissionMeta {
  code: Permission
  name: string
}

/** 权限分组与中文标签，顺序与后端 PERMISSION_CATALOG 一致；权限目录接口不可用时作为兜底。 */
export const PERMISSION_GROUPS: Array<{ group: string; items: PermissionMeta[] }> = [
  { group: '用户', items: [{ code: 'user:manage', name: '用户管理' }] },
  { group: '组织', items: [{ code: 'department:manage', name: '部门管理' }] },
  { group: '角色', items: [{ code: 'role:manage', name: '角色权限管理' }] },
  { group: '审计', items: [{ code: 'audit:read', name: '审计日志查看' }] },
  {
    group: '文档',
    items: [
      { code: 'document:read', name: '文档查看' },
      { code: 'document:write', name: '文档上传' },
      { code: 'document:delete', name: '文档删除' },
    ],
  },
  { group: '解析', items: [{ code: 'parse:run', name: '解析执行' }] },
  {
    group: '本体',
    items: [
      { code: 'ontology:read', name: '本体查看' },
      { code: 'ontology:manage', name: '本体管理' },
      { code: 'ontology:review', name: '本体审核' },
    ],
  },
  { group: '图谱', items: [{ code: 'graph:read', name: '图谱查看' }] },
  {
    group: '审批',
    items: [
      { code: 'review:submit', name: '审批提交' },
      { code: 'review:approve', name: '审批通过' },
    ],
  },
  { group: '问答', items: [{ code: 'qa:manage', name: '问答治理' }] },
]

/** 内置角色的默认中文名；自定义角色由 stores/roles 从接口获取后覆盖。 */
export const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  reviewer: '审批人',
  contributor: '贡献者',
  viewer: '只读',
}

export function permissionLabel(code: string): string {
  for (const group of PERMISSION_GROUPS) {
    const hit = group.items.find((item) => item.code === code)
    if (hit) return hit.name
  }
  return code
}
