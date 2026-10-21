<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'

import type { InferenceTask, TaskStatus } from '@/types/task'
import { formatDuration } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

const props = defineProps<{ task: InferenceTask }>()

const stages: TaskStatus[] = ['queued', 'preparing', 'starting', 'running', 'completed']
const messages: Partial<Record<TaskStatus, string>> = {
  queued: '任务已进入队列，正在等待执行服务领取。',
  preparing: '正在校验输入图片与算法配置。',
  starting: '正在准备算法实例，首次运行需要加载模型，可能要等一会儿。',
  running: '算法正在推理，完成后会自动显示结果。',
  completed: '推理完成，正在读取结果…',
  cancelled: '任务已取消，可以调整参数后再次运行。',
}
const now = ref(Date.now())
const timer = window.setInterval(() => { now.value = Date.now() }, 1000)
onUnmounted(() => window.clearInterval(timer))

const current = computed(() => stages.indexOf(props.task.status))
const elapsed = computed(() => formatDuration(now.value - new Date(props.task.started_at ?? props.task.created_at).getTime()))
</script>

<template>
  <div v-if="task.status === 'failed'" class="alert task-failure" role="alert">
    <strong>推理未完成</strong>
    <p>{{ task.error_message ?? '执行失败，请检查算法和运行资源后重试。' }}</p>
    <small v-if="task.error_code" class="mono">{{ task.error_code }}</small>
  </div>
  <section v-else class="task-progress-panel" role="status">
    <p>
      {{ messages[task.status] }}
      <span v-if="task.status !== 'cancelled' && task.status !== 'completed'" class="muted-copy">已用时 {{ elapsed }}</span>
    </p>
    <ol v-if="task.status !== 'cancelled'" class="task-progress">
      <li v-for="(stage, index) in stages" :key="stage" :class="{ done: index < current, current: index === current }">
        <span>{{ index + 1 }}</span>{{ taskStatusLabel[stage] }}
      </li>
    </ol>
  </section>
</template>
