import { http } from './http'
import type {
  Page,
  ReviewFlow,
  ReviewFlowPage,
  ReviewInstance,
  ReviewInstanceDetail,
} from './types'

export interface ReviewListParams {
  scope?: 'mine' | 'all'
  status?: string
  doc_type?: string
  submitted_by?: string
  document_id?: string
  created_from?: string
  created_to?: string
  page?: number
  page_size?: number
}

export interface FlowPayload {
  name?: string
  doc_type?: string
  description?: string | null
  steps?: Array<{
    name: string
    approver_type: string
    approver_value: string
    skippable: boolean
  }>
  is_default?: boolean
  status?: string
}

export const reviewApi = {
  flows: (params?: { doc_type?: string; include_disabled?: boolean }) =>
    http.get<ReviewFlowPage>('/review-flows', params ?? {}),
  createFlow: (body: FlowPayload) => http.post<ReviewFlow>('/review-flows', body),
  updateFlow: (id: string, body: FlowPayload) =>
    http.patch<ReviewFlow>(`/review-flows/${id}`, body),

  submit: (body: {
    document_id: string
    document_version_id?: string | null
    flow_id?: string | null
    comment?: string | null
  }) => http.post<ReviewInstance>('/reviews/submit', body),
  list: (params: ReviewListParams) => http.get<Page<ReviewInstance>>('/reviews', params),
  detail: (id: string) => http.get<ReviewInstanceDetail>(`/reviews/${id}`),
  approve: (id: string, comment?: string) =>
    http.post<ReviewInstance>(`/reviews/${id}/approve`, { comment: comment ?? null }),
  reject: (id: string, comment: string) =>
    http.post<ReviewInstance>(`/reviews/${id}/reject`, { comment }),
  withdraw: (id: string, comment?: string) =>
    http.post<ReviewInstance>(`/reviews/${id}/withdraw`, { comment: comment ?? null }),
}
