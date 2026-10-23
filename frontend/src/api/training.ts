import { projectUrl, request, uploadRequest } from './http'
import type { Algorithm, ParameterContract } from '@/types/algorithm'
import type { ParameterValues } from '@/utils/parameters'

export interface TrainingModel { key: string; label: string; description: string }
export interface TrainingSource {
  kind: 'builtin' | 'upload' | 'algorithm' | 'run'
  reference?: string
  checkpoint?: 'best' | 'last' | 'resume'
  mode?: 'finetune' | 'resume'
}
export interface TrainingSourceOption { source: TrainingSource; label: string; sha256?: string; validation?: string }
export interface TrainingWeight { id: string; name: string; sha256: string; size_bytes: number; validation: string }
export interface TrainingCheckpoint { resumable: boolean; reason: string | null; epoch: number; epochs: number; sha256: string; file: string }
export interface EvaluationReport {
  split: string; dataset_sha256: string; weight_sha256: string
  metrics: Record<string, number>; speed: Record<string, number>
  per_class: Array<{ class_id: number; name: string; instances: number; precision: number; recall: number; f1: number; map50: number; map50_95: number }>
  confusion_matrix: number[][] | null
}

export interface SplitStats { images: number; labeled: number; backgrounds: number; boxes: number }
export interface DatasetSplitConfig {
  mode: 'preserve' | 'reshuffle'
  train: number; val: number; test: number; seed: number
  validation: 'repair' | 'strict'
}
export const defaultSplitConfig: DatasetSplitConfig = { mode: 'preserve', train: 0.8, val: 0.2, test: 0, seed: 0, validation: 'repair' }
export interface DatasetImage {
  id: number; path: string; source_path: string; split: string; width: number; height: number; sha256: string | null
  boxes: Array<{ class_id: number; cx: number; cy: number; width: number; height: number }>
}
export interface DatasetStats {
  splits: { train: SplitStats; val: SplitStats; test?: SplitStats }
  split_instances?: Record<string, Record<string, number>>
  duplicate_groups?: number
  duplicate_images?: number
  leakage_groups?: number
  instances: Record<string, number>
  warnings: string[]
  invalid_lines: number
  invalid_examples: string[]
  orphan_labels: number
  skipped_files: number
  auto_split: boolean
}
export interface TrainingDataset {
  id: string
  name: string
  format: string
  class_names: string[]
  stats: DatasetStats
  size_bytes: number
  created_by: string
  created_at: string
  family_id: string
  version: number
  parent_id: string | null
  sha256: string
  source_sha256: string | null
  split_config: Partial<DatasetSplitConfig>
}

export type TrainingStatus = 'queued' | 'preparing' | 'running' | 'stopping' | 'completed' | 'stopped' | 'failed'

export interface TrainingParameters extends ParameterValues {
  epochs: number
  batch: number
  imgsz: number
  lr0: number
  optimizer: string
  patience: number
  workers: number
  seed: number
  cos_lr: boolean
}
export interface TrainingProgress {
  operation?: 'train' | 'val'
  source?: TrainingSource & { label: string; sha256?: string; checkpoint_state?: TrainingCheckpoint }
  checkpoint?: TrainingCheckpoint
  dataset?: { id: string; name: string; version: number; sha256: string; source_sha256: string | null; split_config: Partial<DatasetSplitConfig> }
  configuration?: Record<string, unknown>
  phase: 'preparing' | 'train' | 'val' | 'epoch_end'
  epoch: number
  epochs: number
  batch?: number
  batches?: number
  started_at?: number
  updated_at?: number
  losses?: Record<string, number>
}
export interface BestMetrics { epoch: number; precision: number | null; recall: number | null; map50: number | null; map50_95: number | null }
export interface TrainingRunSummary {
  id: string
  name: string
  dataset_id: string
  model: string
  parameters: TrainingParameters
  node_id: string
  gpu_device_ids: string[]
  status: TrainingStatus
  stage: string | null
  progress: TrainingProgress | null
  best: BestMetrics | null
  error: string | null
  created_by: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}
export interface TrainingRun extends TrainingRunSummary {
  metrics: Array<Record<string, number>>
  artifacts: Record<string, number>
}

export const defaultParameters: TrainingParameters = {
  epochs: 100, batch: 16, imgsz: 640, lr0: 0.01, optimizer: 'auto', patience: 50, workers: 4, seed: 0, cos_lr: false,
}

export const trainingApi = {
  parameterContract: (mode: 'train' | 'val' | 'export' = 'train') => request<ParameterContract>(`/training/parameter-contract?mode=${mode}`),
  models: () => request<TrainingModel[]>('/training/models'),
  sources: () => request<TrainingSourceOption[]>('/training/sources'),
  uploadWeight: (file: File, onProgress?: (ratio: number) => void) => {
    const body = new FormData(); body.append('file', file)
    return uploadRequest<TrainingWeight>('/training/weights', body, onProgress)
  },
  deleteWeight: (id: string) => request<void>(`/training/weights/${id}`, { method: 'DELETE' }),
  datasets: () => request<TrainingDataset[]>('/training/datasets'),
  uploadDataset: (file: File, name: string, onProgress?: (ratio: number) => void, config?: DatasetSplitConfig, parentId?: string) => {
    const body = new FormData()
    body.append('file', file)
    body.append('name', name)
    if (config) body.append('split_config', JSON.stringify(config))
    if (parentId) body.append('parent_id', parentId)
    return uploadRequest<TrainingDataset>('/training/datasets', body, onProgress)
  },
  dataset: (id: string) => request<TrainingDataset>(`/training/datasets/${id}`),
  datasetImages: (id: string, split = '', offset = 0) => request<{ total: number; items: DatasetImage[] }>(`/training/datasets/${id}/images?offset=${offset}&limit=12${split ? `&split=${encodeURIComponent(split)}` : ''}`),
  newDatasetVersion: (id: string, config: DatasetSplitConfig) => request<TrainingDataset>(`/training/datasets/${id}/versions`, { method: 'POST', body: JSON.stringify(config) }),
  datasetFeedback: (
    id: string,
    payload: {
      samples: Array<{ task_id: string; boxes?: Array<{ label: string; bbox: [number, number, number, number] }> | null }>
      target_split?: 'train' | 'val' | 'test'
      min_confidence?: number
      split_config?: DatasetSplitConfig
    },
  ) => request<TrainingDataset>(`/training/datasets/${id}/feedback`, { method: 'POST', body: JSON.stringify(payload) }),
  datasetPreviewUrl: (id: string, image: number) => projectUrl(`/training/datasets/${id}/images/${image}/preview`),
  datasetArchiveUrl: (id: string, source = false) => projectUrl(`/training/datasets/${id}/archive?source=${source}`),
  datasetReportUrl: (id: string) => projectUrl(`/training/datasets/${id}/report`),
  deleteDataset: (id: string) => request<void>(`/training/datasets/${id}`, { method: 'DELETE' }),
  runs: () => request<TrainingRunSummary[]>('/training/runs'),
  run: (id: string) => request<TrainingRun>(`/training/runs/${id}`),
  create: (payload: { name: string; dataset_id: string; model: string; node_id: string; gpu_device_ids: string[]; parameters: ParameterValues; source?: TrainingSource; operation?: 'train' | 'val' }) =>
    request<TrainingRun>('/training/runs', { method: 'POST', body: JSON.stringify(payload) }),
  stop: (id: string) => request<TrainingRun>(`/training/runs/${id}/stop`, { method: 'POST' }),
  evaluation: (id: string) => request<EvaluationReport>(`/training/runs/${id}/evaluation`),
  remove: (id: string) => request<void>(`/training/runs/${id}`, { method: 'DELETE' }),
  logs: (id: string, tail = 300) => request<string[]>(`/training/runs/${id}/logs?tail=${tail}`),
  publish: (id: string, payload: { checkpoint: 'best' | 'last'; key: string; name: string; version: string; description: string }) =>
    request<Algorithm>(`/training/runs/${id}/publish`, { method: 'POST', body: JSON.stringify(payload) }),
  artifactUrl: (id: string, name: string) => projectUrl(`/training/runs/${id}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`),
  archiveUrl: (id: string) => projectUrl(`/training/runs/${id}/archive`),
}

export const trainingStatusLabel: Record<TrainingStatus, string> = {
  queued: '排队中', preparing: '准备中', running: '训练中', stopping: '正在停止',
  completed: '已完成', stopped: '已停止', failed: '失败',
}
/** 复用任务状态的配色：task-<键> */
export const trainingStatusTone: Record<TrainingStatus, string> = {
  queued: 'queued', preparing: 'running', running: 'running', stopping: 'running',
  completed: 'completed', stopped: 'cancelled', failed: 'failed',
}
export const activeTrainingStatuses: TrainingStatus[] = ['queued', 'preparing', 'running', 'stopping']

export function progressRatio(run: TrainingRunSummary): number | null {
  const progress = run.progress
  if (!progress || !progress.epochs) return run.status === 'completed' ? 1 : null
  if (run.status === 'completed') return 1
  const within = progress.batches ? (progress.batch ?? 0) / progress.batches : 0
  const finishedEpochs = progress.phase === 'epoch_end' ? progress.epoch : Math.max(0, progress.epoch - 1) + within
  return Math.min(1, finishedEpochs / progress.epochs)
}

export function progressText(run: TrainingRunSummary): string {
  if (run.stage) return run.stage
  const progress = run.progress
  if (run.status === 'running' && (!progress || progress.phase === 'preparing')) return '加载数据和模型'
  if (!progress || !progress.epochs) return ''
  const phase = progress.phase === 'val' ? '验证中' : progress.phase === 'train' && progress.batches ? `批次 ${progress.batch ?? 0}/${progress.batches}` : ''
  return `第 ${progress.epoch}/${progress.epochs} 轮${phase ? ` · ${phase}` : ''}`
}

export function formatMetric(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? (value * 100).toFixed(1) : '—'
}
