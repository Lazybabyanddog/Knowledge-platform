/**
 * 查询改写实验室。
 *
 * 运行接口是只读的：只做理解、改写、双路检索与融合，不写文档、索引或审批数据。
 * 这里复用统一 ``http`` 封装，令牌、刷新与错误信封都由它处理。
 */
import { http } from './http'
import type { RewriteRunParams, RewriteTrace } from './types'

export const queryRewriteApi = {
  run: (params: RewriteRunParams) =>
    http.post<RewriteTrace>('/retrieval/rewrite-lab/run', params),
}
