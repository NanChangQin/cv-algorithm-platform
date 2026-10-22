import { apiBase, projectContext, queryString, request, requestBlob, requestPage } from './http'
import type { CreateTaskPayload, InferenceTask, TaskResultResponse, TaskStatus, TaskSummary } from '@/types/task'

export interface TaskPageQuery {
  status?: readonly TaskStatus[]
  algorithm_version_id?: string
  asset_id?: string
  node_id?: string
  q?: string
  limit: number
  offset?: number
}

export const tasksApi = {
  list: () => request<InferenceTask[]>('/tasks'),
  /** 服务端分页和筛选；总数用于分页器和状态标签。 */
  page: (params: TaskPageQuery) => requestPage<InferenceTask>(`/tasks${queryString({ ...params })}`),
  /** 只刷新指定的任务（轮询当前页里还在运行的任务）。 */
  byIds: (ids: string[]) => ids.length
    ? request<InferenceTask[]>(`/tasks${queryString({ ids })}`)
    : Promise.resolve([] as InferenceTask[]),
  summary: () => request<TaskSummary>('/tasks/summary'),
  get: (id: string) => request<InferenceTask>(`/tasks/${id}`),
  result: (id: string) => request<TaskResultResponse>(`/tasks/${id}/result`),
  create: (payload: CreateTaskPayload) =>
    request<InferenceTask>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  cancel: (id: string) => request<InferenceTask>(`/tasks/${id}/cancel`, { method: 'POST' }),
  retry: (id: string) => request<InferenceTask>(`/tasks/${id}/retry`, { method: 'POST' }),
  archive: (taskIds: string[]) => requestBlob('/tasks/results/archive', {
    method: 'POST', body: JSON.stringify({ task_ids: taskIds }),
  }),
  subscribe: (
    id: string,
    onUpdate: (event: { status: InferenceTask['status'] }) => void,
  ) => {
    const projectId = projectContext.get()
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const source = new EventSource(`${apiBase}/task-events/${id}${query}`, { withCredentials: true })
    source.addEventListener('task', (event: MessageEvent<string>) => onUpdate(JSON.parse(event.data)))
    return source
  },
}
