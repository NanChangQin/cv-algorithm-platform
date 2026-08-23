<script setup lang="ts">
import type { Algorithm } from '@/types/algorithm'
import type { InferenceTask } from '@/types/task'
import { taskStatusLabel } from '@/utils/status'

const props = withDefaults(defineProps<{
  tasks: InferenceTask[]
  algorithms: Algorithm[]
  manageable?: boolean
  selectedResults?: string[]
}>(), {
  manageable: false,
  selectedResults: () => [],
})
defineEmits<{
  'toggle-result': [taskId: string]
  cancel: [taskId: string]
  retry: [taskId: string]
}>()

function algorithmName(id: string) {
  return props.algorithms.find((item) => item.id === id)?.name ?? '未知算法'
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}
</script>

<template>
  <div v-if="tasks.length" class="task-table" :class="{ manageable }">
    <div class="task-row task-head">
      <span>任务</span><span>算法</span><span>状态</span><span>创建时间</span><span v-if="manageable">操作</span>
    </div>
    <div v-for="task in tasks" :key="task.id" class="task-row">
      <RouterLink :to="`/tasks/${task.id}`" class="mono">{{ task.id.slice(0, 8) }}</RouterLink>
      <RouterLink :to="`/tasks/${task.id}`">{{ algorithmName(task.algorithm_version_id) }}</RouterLink>
      <RouterLink :to="`/tasks/${task.id}`" :class="`task-status task-${task.status}`"><i class="status-dot" /> {{ taskStatusLabel[task.status] }}</RouterLink>
      <RouterLink :to="`/tasks/${task.id}`">{{ formatTime(task.created_at) }}</RouterLink>
      <span v-if="manageable" class="task-row-actions">
        <label v-if="task.status === 'completed'" title="加入结果归档">
          <input
            type="checkbox"
            :checked="selectedResults.includes(task.id)"
            @change="$emit('toggle-result', task.id)"
          /> 归档
        </label>
        <button
          v-if="!['completed', 'failed', 'cancelled'].includes(task.status)"
          class="secondary-button danger-button"
          @click="$emit('cancel', task.id)"
        >取消</button>
        <button
          v-if="['failed', 'cancelled'].includes(task.status)"
          class="secondary-button"
          @click="$emit('retry', task.id)"
        >重试</button>
      </span>
    </div>
  </div>
  <div v-else class="empty-state">尚无任务。选择算法并创建第一个推理任务。</div>
</template>
