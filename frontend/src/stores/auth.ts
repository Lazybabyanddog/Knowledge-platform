import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi } from '@/api/auth'
import type { LoginResponse, MeResponse } from '@/api/types'
import type { Permission } from '@/constants'

const AUTH_KEY = 'ai-platform-auth'
const USER_KEY = 'ai-platform-user'

interface StoredAuth {
  accessToken: string
  refreshToken: string
}

function loadAuth(): StoredAuth {
  const raw = localStorage.getItem(AUTH_KEY)
  if (!raw) return { accessToken: '', refreshToken: '' }
  try {
    return JSON.parse(raw) as StoredAuth
  } catch {
    return { accessToken: '', refreshToken: '' }
  }
}

function loadUser(): MeResponse | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as MeResponse
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const stored = loadAuth()
  const accessToken = ref(stored.accessToken)
  const refreshToken = ref(stored.refreshToken)
  const user = ref<MeResponse | null>(loadUser())

  const roles = computed(() => user.value?.roles ?? [])
  const isLoggedIn = computed(() => Boolean(accessToken.value))
  /** 权限直接取 /auth/me 下发的编码，前端不再按角色推导 */
  const permissions = computed(
    () => new Set((user.value?.permissions ?? []) as Permission[]),
  )
  const permissionsLoaded = computed(() => Array.isArray(user.value?.permissions))
  const isAdmin = computed(() => roles.value.includes('admin'))

  function applyLogin(payload: LoginResponse): void {
    accessToken.value = payload.access_token
    refreshToken.value = payload.refresh_token
    localStorage.setItem(
      AUTH_KEY,
      JSON.stringify({
        accessToken: payload.access_token,
        refreshToken: payload.refresh_token,
      }),
    )
  }

  function has(permission: Permission): boolean {
    return permissions.value.has(permission)
  }

  async function login(username: string, password: string): Promise<void> {
    const payload = await authApi.login(username, password)
    applyLogin(payload)
    await fetchMe()
  }

  async function fetchMe(): Promise<MeResponse> {
    const me = await authApi.me()
    user.value = me
    localStorage.setItem(USER_KEY, JSON.stringify(me))
    return me
  }

  /**
   * 本地缓存可能来自旧版本（登录响应只有 roles），缺 permissions 时补拉一次 /auth/me；
   * 补拉后仍没有权限就清空登录态，避免出现"能进页面但没有权限"的半登录状态。
   */
  async function ensurePermissions(): Promise<boolean> {
    if (permissionsLoaded.value) return true
    try {
      await fetchMe()
    } catch {
      clear()
      return false
    }
    if (!permissionsLoaded.value) {
      clear()
      return false
    }
    return true
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout(refreshToken.value || undefined)
    } catch {
      // 令牌可能已失效，忽略即可
    }
    clear()
  }

  function clear(): void {
    accessToken.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem(AUTH_KEY)
    localStorage.removeItem(USER_KEY)
  }

  return {
    accessToken,
    refreshToken,
    user,
    roles,
    isLoggedIn,
    permissions,
    permissionsLoaded,
    isAdmin,
    has,
    login,
    fetchMe,
    ensurePermissions,
    logout,
    clear,
  }
})
