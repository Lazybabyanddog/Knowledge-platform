import { http } from './http'
import type {
  QaAssignee,
  QaDisposition,
  QaFeedbackPayload,
  QaFeedbackResponse,
  QaIssueDetail,
  QaIssueListResponse,
} from './types'

export interface QaIssueListParams {
  status?: string
  disposition?: string
  reason?: string
  assignee_id?: string
  document_id?: string
  /** 最近发生时间下界，ISO 字符串 */
  created_from?: string
  /** 最近发生时间上界，ISO 字符串 */
  created_to?: string
  page?: number
  page_size?: number
}

export const qaApi = {
  /* ------------------------------ 反馈 ------------------------------ */
  getFeedback: (recordId: string) =>
    http.get<QaFeedbackResponse>(`/qa-records/${recordId}/feedback`),
  submitFeedback: (recordId: string, body: QaFeedbackPayload) =>
    http.put<QaFeedbackResponse>(`/qa-records/${recordId}/feedback`, body),

  /* ------------------------------ 治理 ------------------------------ */
  listIssues: (params: QaIssueListParams) =>
    http.get<QaIssueListResponse>('/qa-issues', params),
  issueDetail: (issueId: string) => http.get<QaIssueDetail>(`/qa-issues/${issueId}`),
  assignees: () => http.get<QaAssignee[]>('/qa-issues/assignable-users'),

  assign: (issueId: string, body: { assignee_id: string | null; note?: string | null }) =>
    http.post<QaIssueDetail>(`/qa-issues/${issueId}/assign`, body),
  dispose: (issueId: string, body: { disposition: QaDisposition; note?: string | null }) =>
    http.post<QaIssueDetail>(`/qa-issues/${issueId}/dispose`, body),
  close: (issueId: string, body: { disposition?: QaDisposition | null; note?: string | null }) =>
    http.post<QaIssueDetail>(`/qa-issues/${issueId}/close`, body),
  reopen: (issueId: string, body: { note?: string | null }) =>
    http.post<QaIssueDetail>(`/qa-issues/${issueId}/reopen`, body),
}
