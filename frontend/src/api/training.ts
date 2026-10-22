import { projectUrl, request, uploadRequest } from './http'

export interface TrainingModel { key: string; label: string; description: string }

export interface SplitStats { images: number; labeled: number; backgrounds: number; boxes: number }
export interface DatasetStats {
  splits: { train: SplitStats; val: SplitStats }
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
}

export type TrainingStatus = 'queued' | 'preparing' | 'running' | 'stopping' | 'completed' | 'stopped' | 'failed'

export interface TrainingParameters {
  epochs: number
  batch: number
  imgsz: number
  lr0: number
  optimizer: 'auto' | 'SGD' | 'Adam' | 'AdamW'
  patience: number
  workers: number
  seed: number
  cos_lr: boolean
}
export interface TrainingProgress {
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
  models: () => request<TrainingModel[]>('/training/models'),
  datasets: () => request<TrainingDataset[]>('/training/datasets'),
  uploadDataset: (file: File, name: string, onProgress?: (ratio: number) => void) => {
    const body = new FormData()
    body.append('file', file)
    body.append('name', name)
    return uploadRequest<TrainingDataset>('/training/datasets', body, onProgress)
  },
  deleteDataset: (id: string) => request<void>(`/training/datasets/${id}`, { method: 'DELETE' }),
  runs: () => request<TrainingRunSummary[]>('/training/runs'),
  run: (id: string) => request<TrainingRun>(`/training/runs/${id}`),
  create: (payload: { name: string; dataset_id: string; model: string; node_id: string; gpu_device_ids: string[]; parameters: TrainingParameters }) =>
    request<TrainingRun>('/training/runs', { method: 'POST', body: JSON.stringify(payload) }),
  stop: (id: string) => request<TrainingRun>(`/training/runs/${id}/stop`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/training/runs/${id}`, { method: 'DELETE' }),
  logs: (id: string, tail = 300) => request<string[]>(`/training/runs/${id}/logs?tail=${tail}`),
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
