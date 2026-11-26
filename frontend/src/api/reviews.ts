import { request } from './http'
import type { DetectionBox } from './evaluations'
import type { TrainingDataset } from './training'
export interface SampleReview {
  id: string; task_id: string; asset_id: string; name: string; width: number; height: number
  version: number; status: 'draft' | 'reviewed' | 'published'; boxes: DetectionBox[]
  predictions: DetectionBox[]; ground_truth: DetectionBox[] | null; note: string
  evaluation_id: string | null; publications: Array<{ dataset_id: string; version: number }>
}
export const reviewsApi = {
  list: (status = 'all', offset = 0) => request<{ items: SampleReview[]; total: number }>(`/training/reviews?status=${status}&offset=${offset}`),
  get: (id: string) => request<SampleReview>(`/training/reviews/${id}`),
  collect: (task_id: string, evaluation_id?: string) => request<SampleReview>('/training/reviews', { method: 'POST', body: JSON.stringify({ task_id, evaluation_id }) }),
  save: (item: SampleReview, reviewed: boolean) => request<SampleReview>(`/training/reviews/${item.id}`, { method: 'PUT', body: JSON.stringify({ version: item.version, boxes: item.boxes, note: item.note, reviewed }) }),
  publish: (dataset_id: string, review_ids: string[], target_split: string, acknowledge_evaluation_reuse: boolean) => request<TrainingDataset>('/training/reviews/publish', { method: 'POST', body: JSON.stringify({ dataset_id, review_ids, target_split, acknowledge_evaluation_reuse }) }),
}
