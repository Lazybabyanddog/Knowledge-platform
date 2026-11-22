import { http } from './http'
import type { OntologyClass, OntologyRelation } from './types'

export const ontologyApi = {
  classes: (includeDisabled = false) =>
    http.get<{ items: OntologyClass[]; total: number; version: number }>('/ontology/classes', {
      include_disabled: includeDisabled,
    }),
  createClass: (body: Record<string, unknown>) => http.post<OntologyClass>('/ontology/classes', body),
  updateClass: (id: string, body: Record<string, unknown>) =>
    http.patch<OntologyClass>(`/ontology/classes/${id}`, body),

  relations: (includeDisabled = false) =>
    http.get<{ items: OntologyRelation[]; total: number; version: number }>('/ontology/relations', {
      include_disabled: includeDisabled,
    }),
  createRelation: (body: Record<string, unknown>) =>
    http.post<OntologyRelation>('/ontology/relations', body),
  updateRelation: (id: string, body: Record<string, unknown>) =>
    http.patch<OntologyRelation>(`/ontology/relations/${id}`, body),

  sync: () => http.post<{ projected: number }>('/ontology/sync'),
}
