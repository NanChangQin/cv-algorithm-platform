<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { tasksApi } from '@/api/tasks'
import TaskTable from '@/components/tasks/TaskTable.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useTasks } from '@/composables/useTasks'
import { taskStatusLabel } from '@/utils/status'

const { algorithms } = useAlgorithms()
const { tasks, loading, error, refresh, cancel, retry } = useTasks()
const algorithmFilter = ref('')
const statusFilter = ref('')
const assetFilter = ref('')
const createdAfter = ref('')
const selectedResults = ref<string[]>([])
const page = ref(1)
const pageSize = 20
const taskStatuses = ['queued', 'preparing', 'starting', 'running', 'completed', 'failed', 'cancelled'] as const
const filteredTasks = computed(() => tasks.value.filter((task) =>
  (!algorithmFilter.value || task.algorithm_version_id === algorithmFilter.value)
  && (!statusFilter.value || task.status === statusFilter.value)
  && (!assetFilter.value || task.asset_id?.includes(assetFilter.value.trim()))
  && (!createdAfter.value || task.created_at >= new Date(createdAfter.value).toISOString()),
))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredTasks.value.length / pageSize)))
const pagedTasks = computed(() => filteredTasks.value.slice(
  (page.value - 1) * pageSize,
  page.value * pageSize,
))

watch([algorithmFilter, statusFilter, assetFilter, createdAfter], () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })

async function downloadArchive() {
  const blob = await tasksApi.archive(selectedResults.value)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = 'cv-results.zip'
  link.click()
  URL.revokeObjectURL(link.href)
}

function toggleResult(taskId: string) {
  selectedResults.value = selectedResults.value.includes(taskId)
    ? selectedResults.value.filter((item) => item !== taskId)
    : [...selectedResults.value, taskId]
}
</script>

<template>
  <header class="page-heading"><span class="eyebrow">INFERENCE QUEUE</span><h1>任务中心</h1><p>追踪算法调用、参数快照和执行状态。</p></header>
  <p v-if="error" class="alert">{{ error }}</p>
  <div class="toolbar"><span>共 {{ tasks.length }} 个任务，当前筛选 {{ filteredTasks.length }} 个</span><button class="secondary-button" @click="refresh">刷新</button></div>
  <div class="algorithm-filters">
    <select v-model="algorithmFilter"><option value="">全部算法</option><option v-for="algorithm in algorithms" :key="algorithm.id" :value="algorithm.id">{{ algorithm.name }} · {{ algorithm.version }}</option></select>
    <select v-model="statusFilter"><option value="">全部状态</option><option v-for="status in taskStatuses" :key="status" :value="status">{{ taskStatusLabel[status] }}</option></select>
    <input v-model="assetFilter" placeholder="按资源 ID 查询" />
    <input v-model="createdAfter" type="datetime-local" title="创建时间不早于" />
  </div>
  <div v-if="loading" class="empty-state">正在加载…</div>
  <template v-else>
    <TaskTable
      :tasks="pagedTasks"
      :algorithms="algorithms"
      :selected-results="selectedResults"
      manageable
      @toggle-result="toggleResult"
      @cancel="cancel"
      @retry="retry"
    />
    <nav v-if="pageCount > 1" class="pagination" aria-label="任务分页">
      <button class="secondary-button" :disabled="page === 1" @click="page--">上一页</button>
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <button class="secondary-button" :disabled="page === pageCount" @click="page++">下一页</button>
    </nav>
    <button v-if="selectedResults.length" class="primary-button compact-button archive-button" @click="downloadArchive">下载 {{ selectedResults.length }} 个结果 ZIP</button>
  </template>
</template>
