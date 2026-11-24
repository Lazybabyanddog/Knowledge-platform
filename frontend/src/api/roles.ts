import { http } from './http'
import type { PermissionCatalogResponse, RoleItem, RoleListResponse } from './types'

export interface RolePayload {
  code?: string
  name?: string
  description?: string | null
  permissions?: string[]
}

export const roleApi = {
  list: () => http.get<RoleListResponse>('/roles'),
  permissions: () => http.get<PermissionCatalogResponse>('/roles/permissions'),
  create: (body: RolePayload) => http.post<RoleItem>('/roles', body),
  update: (id: string, body: RolePayload) => http.patch<RoleItem>(`/roles/${id}`, body),
  remove: (id: string) => http.delete<void>(`/roles/${id}`),
}
