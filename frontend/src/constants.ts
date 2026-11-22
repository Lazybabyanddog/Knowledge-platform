/** 与后端 app/domain/policies.py 保持一致的权限矩阵，仅用于前端渲染与守卫。 */

export type Permission =
  | 'user:manage'
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

const ADMIN: Permission[] = [
  'user:manage',
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

const REVIEWER: Permission[] = ['document:read', 'ontology:read', 'graph:read', 'review:approve']

const CONTRIBUTOR: Permission[] = [
  'document:read',
  'document:write',
  'document:delete',
  'parse:run',
  'ontology:read',
  'ontology:review',
  'graph:read',
  'review:submit',
]

const VIEWER: Permission[] = ['document:read', 'ontology:read', 'graph:read']

export const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  admin: ADMIN,
  reviewer: REVIEWER,
  contributor: CONTRIBUTOR,
  viewer: VIEWER,
}

export const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  reviewer: '审批人',
  contributor: '贡献者',
  viewer: '只读',
}

export function permissionsOf(roles: string[]): Set<Permission> {
  const granted = new Set<Permission>()
  for (const role of roles) {
    for (const permission of ROLE_PERMISSIONS[role] ?? []) {
      granted.add(permission)
    }
  }
  return granted
}
