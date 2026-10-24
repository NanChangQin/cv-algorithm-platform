import { projectUrl, queryString, request } from './http'
import type { AlgorithmResult, Detection } from '@/types/task'

export type InputKind = 'images' | 'video' | 'browser' | 'camera' | 'rtsp'
export interface InferenceConfig {
  name: string; kind: InputKind; algorithm_version_id: string; parameters: Record<string, unknown>
  asset_ids: string[]; source_id: string | null; node_id: string | null; gpu_device_ids: string[]; deployment_id: string | null
  tracking: boolean; interval_seconds: number; max_frames: number; duration_seconds: number; reconnect_attempts: number
  save_video: boolean; save_recording: boolean; storage_limit_mb: number; retention_days: number; retry_of?: string | null
}
export interface InferenceSession {
  id: string; name: string; kind: InputKind; status: string; created_at: string; updated_at: string; started_at: string | null; finished_at: string | null
  config: InferenceConfig; model: { id: string; name: string; version: string; task_type: string }; source: { name: string } | null
  processed: number; failed: number; tracks: number; segment: number; reconnections: number; skipped_frames: number
  error: string | null; notes: string[]; artifacts: Record<string, number>; bytes: number; expires_at: string | null
  total_frames: number | null; source_fps: number | null; current_task_id?: string | null
}
export interface InferenceFrame {
  index: number; source_index: number; timestamp_ms: number | null; segment: number; asset_id: string; input_name: string
  task_id: string; status: string; error: string | null; result: AlgorithmResult | null
  tracks: Array<Detection & { track_id: number; trail: number[][] }>
}
export interface InferenceReport {
  snapshot_at: string; session: InferenceSession; scope: Record<string, unknown>; notes: string[]
  summary: { processed: number; failed: number; detections: number; class_counts: Record<string, number>; tracks: number | null; mean_inference_ms: number | null; session_processing_fps: number | null }
  timeline: Array<{ index: number; timestamp_ms: number | null; count: number; inference_ms: number | null }>
}
export const terminal = (status: string) => ['completed', 'failed', 'stopped', 'interrupted'].includes(status)
export const inferenceStatus: Record<string, string> = { queued: '排队中', running: '运行中', reconnecting: '正在重连', stopping: '正在停止', finalizing: '生成视频', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断' }
export const inputLabels: Record<InputKind, string> = { images: '图片', video: '视频文件', browser: '浏览器摄像头', camera: '服务器摄像头', rtsp: 'RTSP' }
export const inferenceApi = {
  create: (config: InferenceConfig) => request<InferenceSession>('/inference/sessions', { method: 'POST', body: JSON.stringify(config) }),
  list: (offset = 0) => request<InferenceSession[]>(`/inference/sessions?limit=30&offset=${offset}`),
  get: (id: string) => request<InferenceSession>(`/inference/sessions/${id}`),
  stop: (id: string) => request<InferenceSession>(`/inference/sessions/${id}/stop`, { method: 'POST' }),
  retry: (id: string) => request<InferenceSession>(`/inference/sessions/${id}/retry`, { method: 'POST' }),
  frames: (id: string, offset = 0, limit = 20) => request<{ items: InferenceFrame[]; total: number }>(`/inference/sessions/${id}/frames?offset=${offset}&limit=${limit}`),
  uploadFrame: (id: string, blob: Blob) => { const body = new FormData(); body.append('file', blob, 'frame.jpg'); return request(`/inference/sessions/${id}/frames`, { method: 'POST', body }) },
  report: (id: string, filters: Record<string, string | number | undefined> = {}) => request<InferenceReport>(`/inference/sessions/${id}/report${queryString(filters)}`),
  download: (id: string, name: string, filters: Record<string, string | number | undefined> = {}) => projectUrl(`/inference/sessions/${id}/download/${name}${queryString(filters)}`),
  file: (id: string, name: string) => projectUrl(`/inference/sessions/${id}/files/${name}`),
  cameras: () => request<Array<{ id: string; uri: string }>>('/inference/cameras'),
}
