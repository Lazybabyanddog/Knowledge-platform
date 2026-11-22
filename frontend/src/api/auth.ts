import { http } from './http'
import type { LoginResponse, MeResponse } from './types'

export const authApi = {
  login: (username: string, password: string) =>
    http.post<LoginResponse>('/auth/login', { username, password }),
  me: () => http.get<MeResponse>('/auth/me'),
  logout: (refreshToken?: string) =>
    http.post<{ revoked: number }>('/auth/logout', refreshToken ? { refresh_token: refreshToken } : {}),
}
