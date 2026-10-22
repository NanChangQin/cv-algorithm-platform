<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Algorithm } from '@/types/algorithm'
import { runtimeStatusLabel, taskTypeLabel } from '@/utils/presentation'

const props = defineProps<{ algorithms: Algorithm[]; modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()

const query = ref('')
const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return props.algorithms
  return props.algorithms.filter((item) => [item.name, item.key, taskTypeLabel[item.task_type] ?? '']
    .some((value) => value.toLowerCase().includes(keyword)))
})
</script>

<template>
  <div class="algorithm-list">
    <input
      v-if="algorithms.length > 5"
      v-model="query"
      type="search"
      placeholder="搜索算法名称或类型"
      aria-label="搜索算法"
    />
    <div role="radiogroup" aria-label="选择算法">
      <button
        v-for="algorithm in filtered"
        :key="algorithm.id"
        type="button"
        role="radio"
        class="algorithm-option"
        :class="{ selected: algorithm.id === modelValue }"
        :aria-checked="algorithm.id === modelValue"
        @click="emit('update:modelValue', algorithm.id)"
      >
        <span class="algorithm-option-name">
          <strong>{{ algorithm.name }}</strong>
          <small>v{{ algorithm.version }} · {{ taskTypeLabel[algorithm.task_type] }} · {{ algorithm.device.toUpperCase() }}</small>
        </span>
        <span
          class="runtime-dot"
          :class="{ running: algorithm.container_status === 'healthy' || algorithm.container_status === 'running' }"
          :title="runtimeStatusLabel[algorithm.container_status] ?? '未启动'"
        />
      </button>
    </div>
    <p v-if="!filtered.length" class="muted-copy">没有匹配“{{ query }}”的可用算法。</p>
  </div>
</template>
