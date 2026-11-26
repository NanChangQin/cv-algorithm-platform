import { projectUrl, request } from './http'
export type BatchContent = 'weights' | 'reports' | 'metrics' | 'all'
export interface BatchRequest { request_id: string; run_ids: string[]; action: 'delete' | 'download'; content: BatchContent }
export interface BatchItem { id: string; name: string; status: 'success' | 'skipped' | 'failed'; reason: string }
export interface BatchJob {
  id: string; request: Omit<BatchRequest, 'request_id'>; status: 'queued' | 'running' | 'completed' | 'failed'
  total: number; processed: number; current: string; items: BatchItem[]; error: string; download_ready: boolean
}
export interface DeleteCheck { id: string; name: string; eligible: boolean; reason: string }
export const trainingBatchesApi = {
  preview: (run_ids: string[]) => request<{items: DeleteCheck[]}>('/training/batches/delete-preview', {method:'POST',body:JSON.stringify({run_ids})}),
  create: (payload: BatchRequest) => request<BatchJob>('/training/batches', {method:'POST',body:JSON.stringify(payload)}),
  get: (id: string) => request<BatchJob>(`/training/batches/${id}`),
  download: (id: string) => projectUrl(`/training/batches/${id}/download`),
}
