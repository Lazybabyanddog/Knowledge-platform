import { http } from './http'
import type {
  Page,
  ParseBlock,
  ParseEngine,
  ParseStrategy,
  ParseTask,
  ParseTaskDetail,
} from './types'

export const parseApi = {
  strategies: (docType?: string) =>
    http.get<{ items: ParseStrategy[] }>('/parse-strategies', docType ? { doc_type: docType } : {}),
  engines: () => http.get<{ items: ParseEngine[] }>('/parse-strategies/engines'),
  createStrategy: (body: {
    name: string
    doc_type: string
    engine: string
    config: Record<string, unknown>
    is_default: boolean
  }) => http.post<ParseStrategy>('/parse-strategies', body),
  updateStrategy: (id: string, body: Record<string, unknown>) =>
    http.patch<ParseStrategy>(`/parse-strategies/${id}`, body),
  deleteStrategy: (id: string) =>
    http.delete<{ deleted: boolean; disabled_versions: number }>(`/parse-strategies/${id}`),

  trigger: (body: { document_version_id: string; strategy_id?: string | null }) =>
    http.post<ParseTask>('/parse-tasks', body),
  listTasks: (params: Record<string, unknown>) => http.get<Page<ParseTask>>('/parse-tasks', params),
  taskDetail: (id: string) => http.get<ParseTaskDetail>(`/parse-tasks/${id}`),
  retry: (id: string) => http.post<ParseTask>(`/parse-tasks/${id}/retry`),
  cancel: (id: string) => http.post<ParseTask>(`/parse-tasks/${id}/cancel`),
  deleteTask: (id: string) => http.delete<{ deleted: boolean }>(`/parse-tasks/${id}`),
  blocks: (id: string, params: Record<string, unknown>) =>
    http.get<Page<ParseBlock>>(`/parse-tasks/${id}/blocks`, params),
}
