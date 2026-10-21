<script setup lang="ts">
import { assetsApi } from '@/api/assets'
import type { Algorithm } from '@/types/algorithm'
import type { ImageAsset } from '@/types/asset'
import type { InferenceTask } from '@/types/task'
import { formatDateTime, nodeLabel } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

const props = withDefaults(defineProps<{
  tasks: InferenceTask[]
  algorithms: Algorithm[]
  manageable?: boolean
  selectedResults?: string[]
  assets?: ImageAsset[]
  nodes?: Array<{ id: string; name: string }>
  pendingId?: string
  /** 抽屉里正在查看的任务，整行高亮 */
  activeId?: string
}>(), {
  manageable: false,
  selectedResults: () => [],
  assets: () => [],
  nodes: () => [],
  pendingId: '',
  activeId: '',
})
defineEmits<{
  open: [taskId: string]
  'toggle-result': [taskId: string]
  cancel: [taskId: string]
  retry: [taskId: string]
}>()

function algorithmName(id: string) {
  return props.algorithms.find((item) => item.id === id)?.name ?? '未知算法'
}
function assetName(id: string | null) {
  return props.assets.find((asset) => asset.id === id)?.original_name ?? (id ? '图片' : '图片推理任务')
}
function placement(task: InferenceTask) {
  if (task.status === 'completed' && !task.execution_node_id && !task.node_id) return '未记录位置'
  return nodeLabel(task.execution_node_id ?? task.node_id, props.nodes)
}
function device(task: InferenceTask) {
  if (task.execution_node_id) return task.execution_gpu_device_ids?.length ? `GPU ${task.execution_gpu_device_ids.join(', ')}` : 'CPU'
  if (task.status === 'completed') return '历史任务'
  return ['failed', 'cancelled'].includes(task.status) ? '未启动' : '等待执行'
}
</script>

<template>
  <div v-if="tasks.length" class="task-table" :class="{ manageable }">
    <div class="task-row task-head">
      <span>输入</span><span>算法 / 图片</span><span>状态</span><span>执行位置</span><span>创建时间</span><span v-if="manageable">操作</span>
    </div>
    <div v-for="task in tasks" :key="task.id" class="task-row" :class="{ active: task.id === activeId }">
      <button type="button" class="task-thumb" :aria-label="`查看任务 ${task.id.slice(0, 8)}`" @click="$emit('open', task.id)">
        <img v-if="task.asset_id" :src="assetsApi.thumbnailUrl(task.asset_id, 160)" alt="" loading="lazy" />
      </button>
      <button type="button" class="task-algorithm" @click="$emit('open', task.id)">
        <strong>{{ algorithmName(task.algorithm_version_id) }}</strong>
        <small>{{ assetName(task.asset_id) }} · <span class="mono">#{{ task.id.slice(0, 8) }}</span></small>
      </button>
      <span :class="`task-status task-${task.status}`"><i class="status-dot" /> {{ taskStatusLabel[task.status] }}</span>
      <span class="task-placement"><strong>{{ placement(task) }}</strong><small>{{ device(task) }}</small></span>
      <span class="task-date">{{ formatDateTime(task.created_at) }}</span>
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
