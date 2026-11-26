<script setup lang="ts">
import { trainingTaskTypes } from '@/utils/trainingTasks'
const selected = defineModel<string>({ default: 'object_detection' })
defineProps<{ locked?: boolean }>()
</script>

<template>
  <div class="training-task-types" role="radiogroup" aria-label="视觉任务类型">
    <button v-for="item in trainingTaskTypes" :key="item.key" type="button" role="radio" :aria-label="item.label" :aria-checked="selected === item.key" :class="{ selected: selected === item.key }" :disabled="!item.available || locked" @click="selected = item.key">
      <strong>{{ item.label }}</strong><small>{{ item.description }}</small><span>{{ item.available ? '已接入' : '待接入' }}</span>
    </button>
  </div>
</template>

<style scoped>
.training-task-types { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin: 16px 0 24px; }
button { display: flex; flex-direction: column; align-items: flex-start; gap: 9px; padding: 16px; border: 1px solid var(--line-strong); border-radius: 12px; background: var(--surface); color: var(--text); text-align: left; }
button.selected { border-color: var(--primary, #2563eb); background: var(--surface-subtle, #f3f6ff); }
button:disabled { cursor: not-allowed; }
button:not(.selected):disabled { opacity: .65; }
small { color: var(--muted); line-height: 1.5; }
span { font-size: 12px; color: var(--muted); }
@media (max-width: 1100px) { .training-task-types { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 700px) { .training-task-types { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 400px) { .training-task-types { grid-template-columns: minmax(0, 1fr); } }
</style>
