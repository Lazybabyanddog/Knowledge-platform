import { http } from './http'
import type { AttributeDef, OntologyClass, OntologyRelation } from './types'

export interface OntologyClassInput {
  code?: string
  name: string
  description: string | null
  parent_id: string | null
  attributes: Record<string, AttributeDef>
  identity_keys: string[]
  status: 0 | 1
}

export interface OntologyRelationInput {
  code?: string
  name: string
  description: string | null
  source_class_id: string
  target_class_id: string
  attributes: Record<string, AttributeDef>
  status: 0 | 1
}

export const ontologyApi = {
  classes: (includeDisabled = false) =>
    http.get<{ items: OntologyClass[]; total: number; version: number }>('/ontology/classes', {
      include_disabled: includeDisabled,
    }),
  createClass: (body: OntologyClassInput & { code: string }) =>
    http.post<OntologyClass>('/ontology/classes', body),
  updateClass: (id: string, body: Omit<OntologyClassInput, 'code'>) =>
    http.patch<OntologyClass>(`/ontology/classes/${id}`, body),
  deleteClass: (id: string) => http.delete<{ deleted: boolean }>(`/ontology/classes/${id}`),

  relations: (includeDisabled = false) =>
    http.get<{ items: OntologyRelation[]; total: number; version: number }>('/ontology/relations', {
      include_disabled: includeDisabled,
    }),
  createRelation: (body: OntologyRelationInput & { code: string }) =>
    http.post<OntologyRelation>('/ontology/relations', body),
  updateRelation: (id: string, body: Omit<OntologyRelationInput, 'code'>) =>
    http.patch<OntologyRelation>(`/ontology/relations/${id}`, body),
  deleteRelation: (id: string) => http.delete<{ deleted: boolean }>(`/ontology/relations/${id}`),

  sync: () => http.post<{ projected: number }>('/ontology/sync'),
}
