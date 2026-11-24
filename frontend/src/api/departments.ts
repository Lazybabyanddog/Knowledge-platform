import { http } from './http'
import type { DepartmentItem, DepartmentListResponse } from './types'

export const departmentApi = {
  list: () => http.get<DepartmentListResponse>('/departments'),
  create: (body: { name: string; parent_id?: string | null; sort_order?: number }) =>
    http.post<DepartmentItem>('/departments', body),
  update: (
    id: string,
    body: { name?: string; parent_id?: string | null; sort_order?: number },
  ) => http.patch<DepartmentItem>(`/departments/${id}`, body),
  remove: (id: string) => http.delete<void>(`/departments/${id}`),
}
