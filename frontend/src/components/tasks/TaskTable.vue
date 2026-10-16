<script setup lang="ts">
import type { Algorithm } from '@/types/algorithm'
import type { InferenceTask } from '@/types/task'
import type { ImageAsset } from '@/types/asset'
import { taskStatusLabel } from '@/utils/status'
import { nodeLabel } from '@/utils/presentation'

const props = withDefaults(defineProps<{
  tasks: InferenceTask[]
  algorithms: Algorithm[]
  manageable?: boolean
  selectedResults?: string[]
  assets?: ImageAsset[]
  nodes?: Array<{ id: string; name: string }>
  pendingId?: string
}>(), {
  manageable: false,
  selectedResults: () => [],
  assets: () => [],
  nodes: () => [],
  pendingId: '',
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
      <span>任务</span><span>算法 / 输入</span><span>状态</span><span>执行位置</span><span>创建时间</span><span v-if="manageable">操作</span>
    </div>
    <div v-for="task in tasks" :key="task.id" class="task-row">
      <RouterLink :to="`/tasks/${task.id}`" class="mono task-id">#{{ task.id.slice(0, 8) }}</RouterLink>
      <RouterLink :to="`/tasks/${task.id}`" class="task-algorithm"><strong>{{ algorithmName(task.algorithm_version_id) }}</strong><small>{{ assets.find((asset) => asset.id === task.asset_id)?.original_name ?? '图片推理任务' }}</small></RouterLink>
      <RouterLink :to="`/tasks/${task.id}`" :class="`task-status task-${task.status}`"><i class="status-dot" /> {{ taskStatusLabel[task.status] }}</RouterLink>
      <span class="task-placement"><strong>{{ task.status === 'completed' && !task.execution_node_id && !task.node_id ? '未记录位置' : nodeLabel(task.execution_node_id ?? task.node_id, nodes) }}</strong><small v-if="task.execution_node_id">{{ task.execution_gpu_device_ids?.length ? `GPU ${task.execution_gpu_device_ids.join(', ')}` : 'CPU' }}</small><small v-else>{{ task.status === 'completed' ? '历史任务' : ['failed', 'cancelled'].includes(task.status) ? '未启动' : '等待执行' }}</small></span>
      <RouterLink :to="`/tasks/${task.id}`" class="task-date">{{ formatTime(task.created_at) }}</RouterLink>
      <span v-if="manageable" class="task-row-actions">
        <label v-if="task.status === 'completed'" title="加入结果归档">
          <input
            type="checkbox"
            :checked="selectedResults.includes(task.id)"
            :aria-label="`选择任务 ${task.id.slice(0, 8)} 的结果`"
            @change="$emit('toggle-result', task.id)"
          /> 归档
        </label>
        <button
          v-if="!['completed', 'failed', 'cancelled'].includes(task.status)"
          class="secondary-button danger-button"
          :disabled="Boolean(pendingId)"
          @click="$emit('cancel', task.id)"
        >取消</button>
        <button
          v-if="['failed', 'cancelled'].includes(task.status)"
          class="secondary-button"
          :disabled="Boolean(pendingId)"
          @click="$emit('retry', task.id)"
        >重试</button>
      </span>
    </div>
  </div>
  <div v-else class="empty-state">尚无任务。选择算法并创建第一个推理任务。</div>
</template>
