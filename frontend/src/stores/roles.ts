/**
 * 角色字典缓存：角色可由管理员自定义，界面上需要把 code 显示成中文名。
 * 没有 role:manage 权限时接口会 403，这里静默降级为内置角色字典，不影响人员/审批流页面。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { roleApi } from '@/api/roles'
import type { RoleItem } from '@/api/types'
import { ROLE_LABELS } from '@/constants'

export const useRolesStore = defineStore('roles', () => {
  const items = ref<RoleItem[]>([])
  const loaded = ref(false)
  const loading = ref(false)

  const labels = computed<Record<string, string>>(() => {
    const map: Record<string, string> = { ...ROLE_LABELS }
    for (const role of items.value) map[role.code] = role.name
    return map
  })

  const options = computed<Array<{ value: string; label: string }>>(() => {
    if (items.value.length) {
      return items.value.map((role) => ({ value: role.code, label: role.name }))
    }
    return Object.entries(ROLE_LABELS).map(([value, label]) => ({ value, label }))
  })

  function label(code: string): string {
    return labels.value[code] ?? code
  }

  async function ensureLoaded(): Promise<void> {
    if (loaded.value || loading.value) return
    loading.value = true
    try {
      const data = await roleApi.list()
      items.value = data.items
    } catch {
      // 无权限或接口异常：保留内置字典
      items.value = []
    } finally {
      loaded.value = true
      loading.value = false
    }
  }

  /** 角色增删改后刷新，保证人员页与审批流下拉立即拿到新角色 */
  async function reload(): Promise<void> {
    loaded.value = false
    await ensureLoaded()
  }

  return { items, loaded, loading, labels, options, label, ensureLoaded, reload }
})
