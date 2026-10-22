import { request } from './http'

export type EvaluationTaskType = 'object_detection' | 'classification' | 'segmentation' | 'ocr' | 'pose_estimation'

export interface EvaluationDataset {
  id: string
  name: string
  task_type: EvaluationTaskType
  item_count: number
  created_by: string
  created_at: string
}
export interface CreatedDataset extends EvaluationDataset {
  unmatched_files: string[]
  unlabeled_images: string[]
}
export interface EvaluationRun {
  id: string
  dataset_id: string
  dataset_name: string
  task_type: EvaluationTaskType | ''
  algorithm_version_ids: string[]
  task_count: number
  finished: boolean
  created_by: string
  created_at: string
}
export interface AlgorithmScore {
  algorithm_version_id: string
  total: number
  completed: number
  failed: number
  active: number
  metrics: Record<string, number>
  worst: Array<{ task_id: string; asset_id: string; value: number }>
}
export interface EvaluationReport {
  task_type: EvaluationTaskType | ''
  primary_metric: string
  finished: boolean
  algorithms: AlgorithmScore[]
}

export const evaluationsApi = {
  datasets: () => request<EvaluationDataset[]>('/evaluations/datasets'),
  createDataset: (payload: {
    name: string; task_type: EvaluationTaskType; annotations_filename: string; annotations: string; asset_ids: string[]; asset_names?: Record<string, string>
  }) => request<CreatedDataset>('/evaluations/datasets', { method: 'POST', body: JSON.stringify(payload) }),
  deleteDataset: (id: string) => request<void>(`/evaluations/datasets/${id}`, { method: 'DELETE' }),
  runs: () => request<EvaluationRun[]>('/evaluations/runs'),
  start: (payload: { dataset_id: string; algorithm_version_ids: string[] }) =>
    request<EvaluationRun>('/evaluations/runs', { method: 'POST', body: JSON.stringify(payload) }),
  report: (id: string) => request<{ run: EvaluationRun; report: EvaluationReport }>(`/evaluations/runs/${id}`),
}
