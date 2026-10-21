<script setup lang="ts">
import type { Algorithm } from '@/types/algorithm'
import { algorithmStatusLabel, runtimeStatusLabel, taskTypeLabel } from '@/utils/presentation'

defineProps<{ algorithm: Algorithm; selected?: boolean }>()
defineEmits<{ select: [algorithm: Algorithm] }>()
</script>

<template>
  <button
    class="algorithm-card"
    :class="{ selected }"
    :aria-pressed="selected"
    type="button"
    @click="$emit('select', algorithm)"
  >
    <div class="card-heading">
      <span class="algorithm-icon">{{ algorithm.name.replace(/[^a-zA-Z0-9\u4e00-\u9fa5]/g, '').slice(0, 2).toUpperCase() }}</span>
      <span class="availability" :class="{ unavailable: algorithm.status !== 'available' }"><i /> {{ algorithmStatusLabel[algorithm.status] ?? algorithm.status }}</span>
    </div>
    <strong>{{ algorithm.name }}</strong>
    <p>{{ algorithm.description }}</p>
    <div class="tag-row">
      <span>{{ taskTypeLabel[algorithm.task_type] }}</span>
      <span>{{ algorithm.framework }}</span>
      <span>{{ algorithm.device.toUpperCase() }}</span>
    </div>
    <footer><span>v{{ algorithm.version }}</span><span>{{ runtimeStatusLabel[algorithm.container_status] ?? '未启动' }}</span></footer>
    <div class="algorithm-last-run"><span>最近调用</span><span>{{ algorithm.last_called_at ? new Date(algorithm.last_called_at).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '暂无记录' }}</span></div>
  </button>
</template>
