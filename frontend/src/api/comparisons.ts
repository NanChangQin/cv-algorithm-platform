import { request } from './http'
import type { AlgorithmComparison, CreateComparisonPayload } from '@/types/comparison'

export const comparisonsApi = {
  list: (limit = 12) => request<AlgorithmComparison[]>(`/comparisons?limit=${limit}`),
  create: (payload: CreateComparisonPayload) =>
    request<AlgorithmComparison>('/comparisons', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  get: (id: string) => request<AlgorithmComparison>(`/comparisons/${id}`),
}
