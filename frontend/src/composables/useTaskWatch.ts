import { computed, onUnmounted, ref, watch, type Ref } from 'vue'

import { tasksApi } from '@/api/tasks'
import type { AlgorithmResult, InferenceTask, TaskStatus } from '@/types/task'
import { errorMessage } from '@/utils/errors'

export const TERMINAL_STATUSES: readonly TaskStatus[] = ['completed', 'failed', 'cancelled']

/**
 * 跟踪一个任务直到结束：服务端推送状态变化，另外每 2 秒轮询兜底（推送断开时也能更新）；
 * 完成后读取结果。taskId 为空字符串时不跟踪。
 */
export function useTaskWatch(taskId: Ref<string>) {
  const task = ref<InferenceTask | null>(null)
  const result = ref<AlgorithmResult | null>(null)
  const error = ref('')
  const terminal = computed(() => Boolean(task.value && TERMINAL_STATUSES.includes(task.value.status)))
  let source: EventSource | null = null
  let loadingId = ''
  let disposed = false

  function close() {
    source?.close()
    source = null
  }

  async function load() {
    const id = taskId.value
    if (!id || loadingId === id || disposed) return
    loadingId = id
    try {
      const loaded = await tasksApi.get(id)
      if (disposed || id !== taskId.value) return
      task.value = loaded
      error.value = ''
      if (loaded.status === 'completed' && !result.value) {
        const response = await tasksApi.result(id)
        if (!disposed && id === taskId.value) result.value = response.result
      }
      if (terminal.value) close()
    } catch (reason) {
      if (id === taskId.value) error.value = errorMessage(reason, '加载任务失败')
    } finally {
      if (loadingId === id) loadingId = ''
    }
  }

  async function open() {
    close()
    task.value = null
    result.value = null
    error.value = ''
    const id = taskId.value
    if (!id) return
    await load()
    if (!disposed && id === taskId.value && task.value && !terminal.value) {
      source = tasksApi.subscribe(id, () => void load())
    }
  }

  /** 取消、重试等操作返回了最新状态时直接替换，不必再等一轮刷新 */
  function replace(updated: InferenceTask) {
    task.value = updated
    if (terminal.value) close()
  }

  watch(taskId, () => void open(), { immediate: true })
  const polling = window.setInterval(() => {
    if (taskId.value && (!terminal.value || (task.value?.status === 'completed' && !result.value))) void load()
  }, 2000)
  onUnmounted(() => {
    disposed = true
    window.clearInterval(polling)
    close()
  })

  return { task, result, error, terminal, reload: load, replace }
}
