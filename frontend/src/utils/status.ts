import type { BuildJob } from '@/types/algorithm'
import type { TaskStatus } from '@/types/task'

export const taskStatusLabel: Record<TaskStatus, string> = {
  queued: '排队中',
  preparing: '准备资源',
  starting: '启动实例',
  running: '推理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const buildStatusLabel: Record<BuildJob['status'], string> = {
  queued: '排队等待',
  building: '构建镜像',
  testing: '协议验收',
  completed: '已发布',
  failed: '构建失败',
}

export const buildProgress: Record<BuildJob['status'], number> = {
  queued: 10,
  building: 55,
  testing: 85,
  completed: 100,
  failed: 100,
}
