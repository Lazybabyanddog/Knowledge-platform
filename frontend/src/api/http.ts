/**
 * 统一 HTTP 封装：
 * 1. 自动附带 Bearer 令牌；
 * 2. 401 时用 refresh_token 刷新一次并重放请求（并发请求共享同一次刷新）；
 * 3. 解包后端 {code, message, data} 信封，失败时抛出 ApiError 并由调用方决定提示。
 */
import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

import type { Envelope, LoginResponse } from './types'

export class ApiError extends Error {
  readonly code: number
  readonly status: number
  readonly details: unknown

  constructor(message: string, code: number, status: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

const TOKEN_KEY = 'ai-platform-auth'

interface StoredAuth {
  accessToken: string
  refreshToken: string
}

let refreshing: Promise<string> | null = null

function readAuth(): StoredAuth | null {
  const raw = localStorage.getItem(TOKEN_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as StoredAuth
  } catch {
    return null
  }
}

function writeTokens(payload: LoginResponse): void {
  localStorage.setItem(
    TOKEN_KEY,
    JSON.stringify({ accessToken: payload.access_token, refreshToken: payload.refresh_token }),
  )
  localStorage.setItem('ai-platform-user', JSON.stringify(payload.user))
}

export const instance = axios.create({
  baseURL: '/api/v1',
  timeout: 60_000,
})

instance.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const auth = readAuth()
  if (auth?.accessToken) {
    config.headers.Authorization = `Bearer ${auth.accessToken}`
  }
  return config
})

async function refreshToken(): Promise<string> {
  const auth = readAuth()
  if (!auth?.refreshToken) {
    throw new ApiError('登录已失效，请重新登录', 40101, 401)
  }
  // 用裸 axios，避免再次进入拦截器造成递归
  const resp = await axios.post<Envelope<LoginResponse>>('/api/v1/auth/refresh', {
    refresh_token: auth.refreshToken,
  })
  writeTokens(resp.data.data)
  return resp.data.data.access_token
}

instance.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error.response?.status as number | undefined
    const config = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
    const url = String(config?.url ?? '')
    const isAuthPath = url.includes('/auth/login') || url.includes('/auth/refresh')

    if (status === 401 && config && !config._retried && !isAuthPath) {
      config._retried = true
      try {
        refreshing ??= refreshToken().finally(() => {
          refreshing = null
        })
        const token = await refreshing
        config.headers = { ...(config.headers ?? {}), Authorization: `Bearer ${token}` }
        return instance.request(config)
      } catch {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem('ai-platform-user')
        if (!location.pathname.startsWith('/login')) {
          location.href = `/login?redirect=${encodeURIComponent(location.pathname + location.search)}`
        }
      }
    }

    const body = error.response?.data as Envelope<unknown> | undefined
    if (body && typeof body.code === 'number') {
      throw new ApiError(body.message || '请求失败', body.code, status ?? 500, body.data)
    }
    if (error.code === 'ECONNABORTED') {
      throw new ApiError('请求超时，请稍后重试', 50000, 0)
    }
    throw new ApiError(error.message || '网络异常', 50000, status ?? 0)
  },
)

export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await instance.request<Envelope<T>>(config)
  return response.data.data
}

export const http = {
  get: <T>(url: string, params?: object) => request<T>({ url, method: 'GET', params }),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ url, method: 'POST', data, ...config }),
  put: <T>(url: string, data?: unknown) => request<T>({ url, method: 'PUT', data }),
  patch: <T>(url: string, data?: unknown) => request<T>({ url, method: 'PATCH', data }),
  delete: <T>(url: string) => request<T>({ url, method: 'DELETE' }),
  upload: <T>(url: string, form: FormData, onProgress?: (percent: number) => void) =>
    request<T>({
      url,
      method: 'POST',
      data: form,
      timeout: 0,
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      },
    }),
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (Array.isArray(error.details) && error.details.length > 0) {
      const first = error.details[0] as { field?: string; message?: string }
      const field = (first.field ?? '').replace(/^(body|query|path)\./, '')
      return field ? `${field}: ${first.message ?? ''}` : (first.message ?? error.message)
    }
    return error.message
  }
  return error instanceof Error ? error.message : '未知错误'
}
