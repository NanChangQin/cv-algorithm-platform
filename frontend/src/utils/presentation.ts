import type { AlgorithmResult, InferenceTask } from '@/types/task'

export const runtimeStatusLabel: Record<string, string> = {
  healthy: '运行中', starting: '启动中', created: '待启动', stopped: '已停止',
  failed: '异常', unknown: '未启动',
}

export const algorithmStatusLabel: Record<string, string> = {
  uploaded: '待构建', validating: '校验中', building: '构建中', testing: '验收中',
  available: '可用', disabled: '已停用', failed: '构建失败',
}

export const taskTypeLabel: Record<string, string> = {
  object_detection: '目标检测', classification: '图像分类', segmentation: '图像分割',
  ocr: '文字识别', pose_estimation: '姿态估计',
}

export const platformRoleLabel: Record<string, string> = {
  admin: '管理员', developer: '算法开发者', user: '普通用户',
}

export const projectRoleLabel: Record<string, string> = {
  owner: '所有者', editor: '编辑者', viewer: '访客',
}

/** 媒体推理、工作流等后台运行的状态 */
export const runStatusLabel: Record<string, string> = {
  queued: '排队中', pending: '等待中', running: '运行中', extracting: '抽帧中', inferring: '推理中',
  completed: '已完成', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断',
}

export function nodeLabel(id: string | null | undefined, nodes: Array<{ id: string; name: string }> = []) {
  if (!id) return '自动调度'
  if (id === 'local') return '主服务器'
  return nodes.find((node) => node.id === id)?.name ?? id
}

export function formatDuration(milliseconds: number) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—'
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} s`
  const minutes = Math.floor(milliseconds / 60_000)
  return `${minutes} 分 ${Math.round((milliseconds % 60_000) / 1000)} 秒`
}

export function taskElapsed(task: InferenceTask) {
  if (!task.started_at) return '—'
  const ended = task.completed_at ?? task.cancelled_at
  if (!ended) return '执行中'
  return formatDuration(new Date(ended).getTime() - new Date(task.started_at).getTime())
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** 毫秒数显示为“12.3 ms”；缺少数据时显示“—” */
export function formatMs(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)} ms` : '—'
}

/** “刚刚 / 5 分钟前 / 3 小时前 / 10月20日” */
export function relativeTime(value: string | null | undefined, now = Date.now()) {
  if (!value) return '—'
  const elapsed = now - new Date(value).getTime()
  if (elapsed < 60_000) return '刚刚'
  if (elapsed < 3_600_000) return `${Math.floor(elapsed / 60_000)} 分钟前`
  if (elapsed < 86_400_000) return `${Math.floor(elapsed / 3_600_000)} 小时前`
  return new Date(value).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
}

/** 结果的一句话概括，例如“3 个目标”“5 段文本” */
export function resultSummary(result: AlgorithmResult | null | undefined) {
  if (!result) return ''
  if (result.type === 'object_detection') return `${result.data.detections.length} 个目标`
  if (result.type === 'classification') return `${result.data.predictions.length} 个分类`
  if (result.type === 'segmentation') return `${result.data.segments.length} 个分割区域`
  if (result.type === 'ocr') return `${result.data.texts.length} 段文本`
  return `${result.data.instances.length} 个姿态`
}

