import type { InferenceTask } from '@/types/task'

export const runtimeStatusLabel: Record<string, string> = {
  healthy: '运行中', starting: '启动中', created: '待启动', stopped: '已停止',
  failed: '异常', unknown: '未启动',
}

export const algorithmStatusLabel: Record<string, string> = {
  uploaded: '待构建', validating: '校验中', building: '构建中', testing: '验收中',
  available: '可用', disabled: '已停用', failed: '构建失败',
}

export function nodeLabel(id: string | null | undefined, nodes: Array<{ id: string; name: string }> = []) {
  if (!id) return '自动调度'
  if (id === 'local') return '主服务器'
  return nodes.find((node) => node.id === id)?.name ?? id
}

export function taskElapsed(task: InferenceTask) {
  if (!task.started_at) return '—'
  const ended = task.completed_at ?? task.cancelled_at
  if (!ended) return '执行中'
  const duration = Math.max(0, new Date(ended).getTime() - new Date(task.started_at).getTime())
  if (!Number.isFinite(duration)) return '—'
  return duration < 1000 ? `${Math.round(duration)} ms` : `${(duration / 1000).toFixed(1)} s`
}
