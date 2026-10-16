import { onMounted, onUnmounted, ref } from 'vue'

import { tasksApi } from '@/api/tasks'
import type { CreateTaskPayload, InferenceTask } from '@/types/task'

export function useTasks() {
  const tasks = ref<InferenceTask[]>([])
  const loading = ref(false)
  const submitting = ref(false)
  const error = ref('')
  const pendingId = ref('')
  let refreshing = false

  async function refresh(options: { silent?: boolean } = {}) {
    if (refreshing) return
    refreshing = true
    if (!options.silent) loading.value = true
    error.value = ''
    try {
      tasks.value = await tasksApi.list()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '加载任务失败'
    } finally {
      loading.value = false
      refreshing = false
    }
  }

  async function create(payload: CreateTaskPayload) {
    submitting.value = true
    error.value = ''
    try {
      const task = await tasksApi.create(payload)
      tasks.value = [task, ...tasks.value]
      return task
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '创建任务失败'
      throw reason
    } finally {
      submitting.value = false
    }
  }

  async function cancel(id: string) {
    if (pendingId.value) return
    pendingId.value = id
    error.value = ''
    try {
      const task = await tasksApi.cancel(id)
      replaceTask(task)
      return task
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '取消任务失败'
    } finally { pendingId.value = '' }
  }

  async function retry(id: string) {
    if (pendingId.value) return
    pendingId.value = id
    error.value = ''
    try {
      const task = await tasksApi.retry(id)
      tasks.value.unshift(task)
      return task
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '重试任务失败'
    } finally { pendingId.value = '' }
  }

  function replaceTask(task: InferenceTask) {
    const index = tasks.value.findIndex((item) => item.id === task.id)
    if (index >= 0) tasks.value[index] = task
  }

  onMounted(() => refresh())

  const polling = window.setInterval(() => {
    if (tasks.value.some((task) => !['completed', 'failed', 'cancelled'].includes(task.status))) {
      void refresh({ silent: true })
    }
  }, 2000)
  onUnmounted(() => window.clearInterval(polling))

  return { tasks, loading, submitting, pendingId, error, refresh, create, cancel, retry }
}
