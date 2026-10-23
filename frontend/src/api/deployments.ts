import { request } from './http'

export type DeploymentStatus = 'queued' | 'preparing' | 'running' | 'draining' | 'stopping' | 'stopped' | 'failed'
export type StepStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed'
export interface DeploymentStep { key: 'node' | 'image' | 'weights' | 'start' | 'verify'; label: string; status: StepStatus; detail: string | null }
export interface RuntimeProfile {
  memory_mb: number | null; cpu_count: number; shm_mb: number; prediction_timeout_seconds: number
  threads: number; warmup_runs: number; defaults: import('@/utils/parameters').ParameterValues
  allowed_overrides: string[] | null
}
export interface Deployment {
  runtime_profile?: RuntimeProfile | null
  readiness?: Record<string, unknown> | null
  in_flight_tasks?: number
  id: string
  algorithm_version_id: string
  node_id: string
  gpu_device_ids: string[]
  status: DeploymentStatus
  steps: DeploymentStep[]
  instance_id: string | null
  error: string | null
  created_by: string
  created_at: string
  updated_at: string
  started_at: string | null
}

export type RouteRolloutStatus = 'active' | 'switching' | 'draining' | 'degraded'
export interface DeploymentRoute {
  id: string
  key: string
  name: string
  active_deployment_id: string
  active_algorithm_version_id: string | null
  previous_deployment_id: string | null
  previous_algorithm_version_id: string | null
  candidate_deployment_id: string | null
  candidate_algorithm_version_id: string | null
  rollout_status: RouteRolloutStatus
  drain_timeout_seconds: number
  last_error: string | null
  updated_by: string
  created_at: string
  updated_at: string
}

export const deploymentsApi = {
  list: () => request<Deployment[]>('/deployments'),
  create: (payload: { algorithm_version_id: string; node_id: string; gpu_device_ids: string[]; runtime_profile?: RuntimeProfile }) =>
    request<Deployment>('/deployments', { method: 'POST', body: JSON.stringify(payload) }),
  stop: (id: string) => request<Deployment>(`/deployments/${id}/stop`, { method: 'POST' }),
  start: (id: string) => request<Deployment>(`/deployments/${id}/start`, { method: 'POST' }),
  redeploy: (id: string) => request<Deployment>(`/deployments/${id}/redeploy`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/deployments/${id}`, { method: 'DELETE' }),
  listRoutes: () => request<DeploymentRoute[]>('/deployments/routes'),
  saveRoute: (key: string, payload: { name?: string; target_deployment_id: string; drain_timeout_seconds?: number }) =>
    request<DeploymentRoute>(`/deployments/routes/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify(payload) }),
  rollbackRoute: (key: string) =>
    request<DeploymentRoute>(`/deployments/routes/${encodeURIComponent(key)}/rollback`, { method: 'POST' }),
  removeRoute: (key: string) =>
    request<void>(`/deployments/routes/${encodeURIComponent(key)}`, { method: 'DELETE' }),
}

export const deploymentStatusLabel: Record<DeploymentStatus, string> = {
  queued: '等待部署', preparing: '部署中', running: '运行中', draining: '正在排空请求', stopping: '正在停止', stopped: '已停止', failed: '失败',
}
/** 复用任务状态的配色：task-<键> */
export const deploymentStatusTone: Record<DeploymentStatus, string> = {
  queued: 'queued', preparing: 'running', running: 'completed', draining: 'running', stopping: 'running', stopped: 'cancelled', failed: 'failed',
}
export const activeDeploymentStatuses: DeploymentStatus[] = ['queued', 'preparing', 'draining', 'stopping']
export const routeStatusLabel: Record<RouteRolloutStatus, string> = {
  active: '服务正常', switching: '待新版本就绪切换', draining: '已切换（旧实例排空中）', degraded: '部署异常',
}

