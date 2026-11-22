import { http } from './http'
import type {
  BatchUploadResponse,
  DocumentItem,
  DocumentVersion,
  DownloadUrl,
  Page,
} from './types'

export interface DocumentQueryParams {
  keyword?: string
  status?: string
  tag?: string
  sensitivity?: string
  owner_id?: string
  created_from?: string
  created_to?: string
  sort_by?: 'created_at' | 'updated_at'
  order?: 'asc' | 'desc'
  include_deleted?: boolean
  page?: number
  page_size?: number
}

export const documentApi = {
  list: (params: DocumentQueryParams) => http.get<Page<DocumentItem>>('/documents', params),
  detail: (id: string) => http.get<DocumentItem>(`/documents/${id}`),
  update: (id: string, body: Record<string, unknown>) =>
    http.patch<DocumentItem>(`/documents/${id}`, body),
  remove: (id: string) => http.delete<{ deleted: boolean }>(`/documents/${id}`),
  restore: (id: string) => http.post<DocumentItem>(`/documents/${id}/restore`),
  versions: (id: string) => http.get<DocumentVersion[]>(`/documents/${id}/versions`),
  download: (id: string, versionId: string) =>
    http.get<DownloadUrl>(`/documents/${id}/versions/${versionId}/download`),
  upload: (form: FormData, onProgress?: (percent: number) => void) =>
    http.upload<DocumentItem>('/documents', form, onProgress),
  uploadBatch: (form: FormData, onProgress?: (percent: number) => void) =>
    http.upload<BatchUploadResponse>('/documents/batch', form, onProgress),
  uploadVersion: (id: string, form: FormData, onProgress?: (percent: number) => void) =>
    http.upload<DocumentVersion>(`/documents/${id}/versions`, form, onProgress),
}
