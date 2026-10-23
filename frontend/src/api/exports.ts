import type { Algorithm } from '@/types/algorithm'
import { projectUrl, request } from './http'
import type { TrainingSource, TrainingStatus } from './training'
import type { ParameterValues } from '@/utils/parameters'

export interface ExportPolicy {
  parameters: ParameterValues; max_map_drop: number; warmup: number; iterations: number; threads: number
}
export interface ExportJob {
  id: string; name: string; status: TrainingStatus; stage: string | null; error: string | null
  dataset_id: string; model: string; node_id: string; parameters: ParameterValues
  artifacts: Record<string, number>; created_at: string
  progress: { phase?: string; source?: { label: string }; export_verification?: ExportPolicy; configuration?: unknown } | null
}
export interface ExportBenchmark {
  mean_ms_per_image: number; p50_ms_per_image: number; p95_ms_per_image: number
  images_per_second: number; iterations: number; warmup: number; scope: string
}
export interface ExportReport {
  format: string; precision: string; artifact: string; artifact_sha256: string; source_sha256: string
  dataset_sha256: string; split: string; input_shape: number[]; dynamic: boolean; opset: number
  quality_gate: { passed: boolean; map50_95_drop: number; max_map_drop: number }
  reference_metrics: Record<string, number>; onnx_metrics: Record<string, number>; metric_deltas: Record<string, number>
  benchmark: { reference: ExportBenchmark; onnx: ExportBenchmark }
}
export const exportsApi = {
  publish: (id: string, payload: { key: string; name: string; version: string }) => request<Algorithm>(`/exports/${id}/publish`, { method: 'POST', body: JSON.stringify(payload) }),
  list: () => request<ExportJob[]>('/exports'),
  get: (id: string) => request<ExportJob>(`/exports/${id}`),
  create: (payload: { name: string; dataset_id: string; node_id: string; model: string; source: TrainingSource; parameters: ParameterValues; verification: ExportPolicy }) => request<ExportJob>('/exports', { method: 'POST', body: JSON.stringify(payload) }),
  stop: (id: string) => request<ExportJob>(`/exports/${id}/stop`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/exports/${id}`, { method: 'DELETE' }),
  logs: (id: string) => request<string[]>(`/exports/${id}/logs`),
  report: (id: string) => request<ExportReport>(`/exports/${id}/report`),
  artifactUrl: (id: string, name: string) => projectUrl(`/exports/${id}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`),
  archiveUrl: (id: string) => projectUrl(`/exports/${id}/archive`),
}
