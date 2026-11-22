import { http } from './http'
import type { Page, UserItem } from './types'

export const userApi = {
  list: (params: Record<string, unknown>) => http.get<Page<UserItem>>('/users', params),
  create: (body: Record<string, unknown>) => http.post<UserItem>('/users', body),
  update: (id: string, body: Record<string, unknown>) => http.patch<UserItem>(`/users/${id}`, body),
  resetPassword: (id: string, newPassword: string) =>
    http.post<{ revoked_tokens: number }>(`/users/${id}/reset-password`, {
      new_password: newPassword,
    }),
}
