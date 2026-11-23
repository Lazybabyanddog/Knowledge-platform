import { http } from './http'
import type {
  EntityItem,
  EntitySource,
  ExtractionTask,
  ExtractionTaskDetail,
  MergeResult,
  Page,
  RelationItem,
  Subgraph,
} from './types'

export const knowledgeApi = {
  triggerExtraction: (body: { parse_task_id: string; limit_blocks?: number | null }) =>
    http.post<ExtractionTask>('/extraction-tasks', body),
  listExtractions: (params: Record<string, unknown>) =>
    http.get<Page<ExtractionTask>>('/extraction-tasks', params),
  extraction: (id: string) => http.get<ExtractionTaskDetail>(`/extraction-tasks/${id}`),
  /** 从断点继续；degrade 为真时同时把批大小减半（占用降参预算） */
  retryExtraction: (id: string, degrade = false) =>
    http.post<ExtractionTask>(`/extraction-tasks/${id}/retry`, { degrade }),
  skipFailedBatches: (id: string) =>
    http.post<ExtractionTask>(`/extraction-tasks/${id}/skip-failed-batches`),
  cancelExtraction: (id: string) => http.post<ExtractionTask>(`/extraction-tasks/${id}/cancel`),

  entities: (params: Record<string, unknown>) => http.get<Page<EntityItem>>('/entities', params),
  entity: (id: string) => http.get<EntityItem>(`/entities/${id}`),
  updateEntity: (id: string, body: Record<string, unknown>) =>
    http.patch<EntityItem>(`/entities/${id}`, body),
  confirmEntity: (id: string, body: Record<string, unknown> = {}) =>
    http.post<EntityItem>(`/entities/${id}/confirm`, body),
  rejectEntity: (id: string, reason: string) =>
    http.post<EntityItem>(`/entities/${id}/reject`, { reason }),
  mergeEntities: (body: { target_id: string; source_ids: string[] }) =>
    http.post<MergeResult>('/entities/merge', body),
  undoMerge: (body: { target_id: string; merge_id?: string | null }) =>
    http.post<Record<string, unknown>>('/entities/merge/undo', body),
  entitySources: (id: string) => http.get<EntitySource>(`/entities/${id}/sources`),

  relations: (params: Record<string, unknown>) => http.get<Page<RelationItem>>('/relations', params),
  relation: (id: string) => http.get<RelationItem>(`/relations/${id}`),
  confirmRelation: (id: string) => http.post<RelationItem>(`/relations/${id}/confirm`),
  rejectRelation: (id: string, reason: string) =>
    http.post<RelationItem>(`/relations/${id}/reject`, { reason }),
  /** 只允许改属性与描述：端点与类型是原文事实，不可篡改 */
  updateRelation: (id: string, body: { attributes?: Record<string, unknown>; description?: string | null }) =>
    http.patch<RelationItem>(`/relations/${id}`, body),

  subgraph: (entityId: string, params: { depth: number; include_proposed: boolean }) =>
    http.get<Subgraph>(`/graph/entities/${entityId}/subgraph`, params),
  documentEntities: (documentId: string, params: Record<string, unknown>) =>
    http.get<Page<EntityItem>>(`/graph/documents/${documentId}/entities`, params),
}
