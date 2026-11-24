import type { DepartmentItem } from '@/api/types'

export interface DepartmentOption {
  value: string
  label: string
  depth: number
  parent_id: string | null
  name: string
  user_count: number
}

function bySort(a: DepartmentItem, b: DepartmentItem): number {
  if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order
  return a.name.localeCompare(b.name, 'zh-CN')
}

/**
 * 后端按邻接表返回部门，这里按 parent_id 还原成树并摊平成下拉选项，
 * label 用全角空格缩进以体现层级。
 */
export function flattenDepartments(items: DepartmentItem[]): DepartmentOption[] {
  const known = new Set(items.map((item) => item.id))
  const children = new Map<string | null, DepartmentItem[]>()
  for (const item of items) {
    // 父节点不在列表里（数据不一致）时按根节点展示，避免整棵子树被丢弃
    const key = item.parent_id && known.has(item.parent_id) ? item.parent_id : null
    const list = children.get(key) ?? []
    list.push(item)
    children.set(key, list)
  }

  const result: DepartmentOption[] = []
  const walk = (parentId: string | null, depth: number): void => {
    const list = [...(children.get(parentId) ?? [])].sort(bySort)
    for (const item of list) {
      result.push({
        value: item.id,
        label: `${'　'.repeat(depth)}${item.name}`,
        depth,
        parent_id: item.parent_id,
        name: item.name,
        user_count: item.user_count,
      })
      walk(item.id, depth + 1)
    }
  }
  walk(null, 0)
  return result
}
