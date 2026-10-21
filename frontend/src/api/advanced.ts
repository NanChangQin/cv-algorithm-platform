import { request } from './http'

export interface MediaSource {
  id: string; name: string; source_type: 'video' | 'rtsp' | 'camera'; uri: string; enabled: boolean
}
export interface WorkflowNode {
  id: string; algorithm_version_id: string; parameters: Record<string, unknown>; depends_on: string[]
}
export interface Workflow {
  id: string; name: string; mode: 'sequential' | 'parallel'; nodes: WorkflowNode[]; created_at: string
}
export interface RuntimeNode { id: string; name: string; manager_url: string; enabled: boolean }
export interface MediaRun {
  id: string; source_id: string; algorithm_version_id: string; status: string; frame_task_ids: string[]; error_message: string | null; created_at: string; updated_at: string
}
export interface WorkflowRun {
  id: string; workflow_id: string; asset_id: string; status: string; node_tasks: Record<string, string>; error_message: string | null; created_at: string
}
export interface ScalingPolicy {
  algorithm_version_id: string; min_replicas: number; max_replicas: number; target_concurrency: number; idle_seconds: number
}

export const advancedApi = {
  mediaSources: () => request<MediaSource[]>('/media-sources'),
  createMediaSource: (payload: Omit<MediaSource, 'id' | 'enabled'>) =>
    request<MediaSource>('/media-sources', { method: 'POST', body: JSON.stringify(payload) }),
  uploadVideo: (file: File) => {
    const body = new FormData(); body.append('file', file)
    return request<MediaSource>('/media-sources/upload', { method: 'POST', body })
  },
  mediaRuns: () => request<MediaRun[]>('/media-sources/runs'),
  deleteMediaSource: (id: string) => request<void>(`/media-sources/${id}`, { method: 'DELETE' }),
  startMediaRun: (sourceId: string, payload: { algorithm_version_id: string; parameters: Record<string, unknown>; interval_seconds: number; max_frames: number }) =>
    request<MediaRun>(`/media-sources/${sourceId}/runs`, { method: 'POST', body: JSON.stringify(payload) }),
  workflows: () => request<Workflow[]>('/workflows'),
  createWorkflow: (payload: Omit<Workflow, 'id' | 'created_at'>) =>
    request<Workflow>('/workflows', { method: 'POST', body: JSON.stringify(payload) }),
  workflowRuns: () => request<WorkflowRun[]>('/workflows/runs'),
  cancelWorkflowRun: (id: string) => request<WorkflowRun>(`/workflows/runs/${id}/cancel`, { method: 'POST' }),
  startWorkflow: (workflowId: string, assetId: string) =>
    request<WorkflowRun>(`/workflows/${workflowId}/runs`, { method: 'POST', body: JSON.stringify({ asset_id: assetId }) }),
  runtimeNodes: () => request<RuntimeNode[]>('/runtime-nodes'),
  registerNode: (payload: RuntimeNode) =>
    request<RuntimeNode>('/runtime-nodes', { method: 'POST', body: JSON.stringify(payload) }),
  metrics: () => request<Record<string, unknown>>('/operations/metrics'),
  scalingPolicies: () => request<ScalingPolicy[]>('/operations/autoscaling'),
  setScalingPolicy: (algorithmId: string, payload: Omit<ScalingPolicy, 'algorithm_version_id'>) =>
    request<ScalingPolicy>(`/operations/autoscaling/${algorithmId}`, { method: 'PUT', body: JSON.stringify(payload) }),
  reconcileScaling: () => request<Record<string, unknown>>('/operations/autoscaling/actions/reconcile', { method: 'POST' }),
  setTraffic: (weights: Record<string, number>) =>
    request('/operations/traffic', { method: 'POST', body: JSON.stringify({ weights }) }),
  deploymentManifest: (id: string, backend: 'docker' | 'bentoml' | 'kserve') =>
    request<{ files: Record<string, string> }>(`/deployment-manifests/${id}?backend=${backend}`),
}
