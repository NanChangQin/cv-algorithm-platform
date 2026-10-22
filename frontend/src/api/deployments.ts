import { request } from './http'

export type DeploymentStatus = 'queued' | 'preparing' | 'running' | 'stopping' | 'stopped' | 'failed'
export type StepStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed'
export interface DeploymentStep { key: 'node' | 'image' | 'weights' | 'start'; label: string; status: StepStatus; detail: string | null }
export interface Deployment {
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

export const deploymentsApi = {
  list: () => request<Deployment[]>('/deployments'),
  create: (payload: { algorithm_version_id: string; node_id: string; gpu_device_ids: string[] }) =>
    request<Deployment>('/deployments', { method: 'POST', body: JSON.stringify(payload) }),
  stop: (id: string) => request<Deployment>(`/deployments/${id}/stop`, { method: 'POST' }),
  start: (id: string) => request<Deployment>(`/deployments/${id}/start`, { method: 'POST' }),
  redeploy: (id: string) => request<Deployment>(`/deployments/${id}/redeploy`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/deployments/${id}`, { method: 'DELETE' }),
}

export const deploymentStatusLabel: Record<DeploymentStatus, string> = {
  queued: '等待部署', preparing: '部署中', running: '运行中', stopping: '正在停止', stopped: '已停止', failed: '失败',
}
/** 复用任务状态的配色：task-<键> */
export const deploymentStatusTone: Record<DeploymentStatus, string> = {
  queued: 'queued', preparing: 'running', running: 'completed', stopping: 'running', stopped: 'cancelled', failed: 'failed',
}
export const activeDeploymentStatuses: DeploymentStatus[] = ['queued', 'preparing', 'stopping']
