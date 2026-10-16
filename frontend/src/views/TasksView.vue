<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { tasksApi } from '@/api/tasks'
import { systemApi } from '@/api/system'
import TaskTable from '@/components/tasks/TaskTable.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssets } from '@/composables/useAssets'
import { useTasks } from '@/composables/useTasks'
import { taskStatusLabel } from '@/utils/status'
import { nodeLabel } from '@/utils/presentation'

const { algorithms } = useAlgorithms()
const { assets } = useAssets()
const { tasks, loading, pendingId, error, refresh, cancel, retry } = useTasks()
const nodes = ref<Array<{ id: string; name: string }>>([])
const query = ref('')
const algorithmFilter = ref('')
const statusFilter = ref('')
const nodeFilter = ref('')
const selectedResults = ref<string[]>([])
const page = ref(1)
const pageSize = 20
const downloading = ref(false)
const downloadError = ref('')
const activeStatuses = ['queued', 'preparing', 'starting', 'running']
const taskStatuses = ['queued', 'preparing', 'starting', 'running', 'completed', 'failed', 'cancelled'] as const
const counts = computed(() => ({
  all: tasks.value.length,
  active: tasks.value.filter((task) => activeStatuses.includes(task.status)).length,
  completed: tasks.value.filter((task) => task.status === 'completed').length,
  failed: tasks.value.filter((task) => task.status === 'failed').length,
}))
const filteredTasks = computed(() => tasks.value.filter((task) => {
  const search = query.value.trim().toLowerCase()
  const algorithm = algorithms.value.find((item) => item.id === task.algorithm_version_id)
  const asset = assets.value.find((item) => item.id === task.asset_id)
  return (!algorithmFilter.value || task.algorithm_version_id === algorithmFilter.value)
    && (!statusFilter.value || (statusFilter.value === 'active' ? activeStatuses.includes(task.status) : task.status === statusFilter.value))
    && (!nodeFilter.value || (task.execution_node_id ?? task.node_id) === nodeFilter.value)
    && (!search || [task.id, algorithm?.name, asset?.original_name, nodeLabel(task.execution_node_id ?? task.node_id, nodes.value)].some((value) => value?.toLowerCase().includes(search)))
}))
const pageCount = computed(() => Math.max(1, Math.ceil(filteredTasks.value.length / pageSize)))
const pagedTasks = computed(() => filteredTasks.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([query, algorithmFilter, statusFilter, nodeFilter], () => { page.value = 1 })
watch(pageCount, (count) => { if (page.value > count) page.value = count })
onMounted(async () => {
  try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* Filters can still use the task's recorded node id. */ }
})
function clearFilters() { query.value = ''; algorithmFilter.value = ''; statusFilter.value = ''; nodeFilter.value = '' }
function toggleResult(taskId: string) {
  downloadError.value = ''
  if (selectedResults.value.includes(taskId)) selectedResults.value = selectedResults.value.filter((item) => item !== taskId)
  else if (selectedResults.value.length < 100) selectedResults.value.push(taskId)
  else downloadError.value = '一次最多归档 100 个结果。'
}
function selectPageResults() {
  selectedResults.value = [...new Set([...selectedResults.value, ...pagedTasks.value.filter((task) => task.status === 'completed').map((task) => task.id)])].slice(0, 100)
}
async function downloadArchive() {
  downloading.value = true
  downloadError.value = ''
  try {
    const blob = await tasksApi.archive(selectedResults.value)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob); link.download = 'cv-results.zip'; link.click(); URL.revokeObjectURL(link.href)
  } catch (reason) { downloadError.value = reason instanceof Error ? reason.message : '结果下载失败' }
  finally { downloading.value = false }
}
</script>

<template>
  <header class="page-heading page-heading-actions"><div><h1>任务中心</h1><p>查看推理进度、执行位置与结果，快速找到需要的任务。</p></div><RouterLink class="primary-button compact-button" to="/">新建推理 →</RouterLink></header>
  <p v-if="error || downloadError" class="alert" role="alert">{{ error || downloadError }}</p>
  <div class="task-overview">
    <button :class="{ selected: !statusFilter }" @click="statusFilter = ''"><span>全部任务</span><strong>{{ counts.all }}</strong></button>
    <button :class="{ selected: statusFilter === 'active' }" @click="statusFilter = 'active'"><span>进行中</span><strong>{{ counts.active }}</strong></button>
    <button :class="{ selected: statusFilter === 'completed' }" @click="statusFilter = 'completed'"><span>已完成</span><strong class="success-text">{{ counts.completed }}</strong></button>
    <button :class="{ selected: statusFilter === 'failed' }" @click="statusFilter = 'failed'"><span>失败任务</span><strong>{{ counts.failed }}</strong></button>
  </div>
  <section class="section-block task-list-panel">
    <div class="algorithm-filters task-filters">
      <input v-model="query" type="search" aria-label="搜索任务" placeholder="搜索任务、算法、图片或服务器" />
      <select v-model="algorithmFilter" aria-label="按算法筛选"><option value="">全部算法</option><option v-for="algorithm in algorithms" :key="algorithm.id" :value="algorithm.id">{{ algorithm.name }}</option></select>
      <select v-model="statusFilter" aria-label="按状态筛选"><option value="">全部状态</option><option value="active">所有进行中</option><option v-for="status in taskStatuses" :key="status" :value="status">{{ taskStatusLabel[status] }}</option></select>
      <select v-model="nodeFilter" aria-label="按服务器筛选"><option value="">全部服务器</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option></select>
    </div>
    <div class="toolbar"><span>{{ filteredTasks.length }} 个匹配任务</span><div class="toolbar-actions"><button v-if="query || algorithmFilter || statusFilter || nodeFilter" class="text-button" @click="clearFilters">清除筛选</button><button class="secondary-button" :disabled="loading" @click="refresh()">{{ loading ? '刷新中…' : '刷新列表' }}</button></div></div>
    <div v-if="loading && !tasks.length" class="empty-state">正在加载任务…</div>
    <div v-else-if="!filteredTasks.length" class="empty-state"><strong>{{ tasks.length ? '没有匹配的任务' : '还没有推理任务' }}</strong><p>{{ tasks.length ? '修改或清除筛选条件后再试。' : '在工作台选择算法和图片，开始第一次推理。' }}</p><button v-if="tasks.length" class="secondary-button" @click="clearFilters">清除筛选</button><RouterLink v-else class="secondary-button" to="/">前往工作台</RouterLink></div>
    <TaskTable v-else :tasks="pagedTasks" :algorithms="algorithms" :assets="assets" :nodes="nodes" :pending-id="pendingId" :selected-results="selectedResults" manageable @toggle-result="toggleResult" @cancel="cancel" @retry="retry" />
    <div v-if="filteredTasks.length" class="list-footer"><div class="toolbar-actions"><button class="text-button" @click="selectPageResults">选择本页已完成结果</button><span v-if="selectedResults.length">已选 {{ selectedResults.length }} 项</span><button v-if="selectedResults.length" class="text-button" @click="selectedResults = []">清空选择</button></div><button v-if="selectedResults.length" class="primary-button compact-button" :disabled="downloading" @click="downloadArchive">{{ downloading ? '打包中…' : '下载结果 ZIP' }}</button></div>
    <nav v-if="pageCount > 1" class="pagination" aria-label="任务分页"><button class="secondary-button" :disabled="page === 1" @click="page--">上一页</button><span>{{ page }} / {{ pageCount }}</span><button class="secondary-button" :disabled="page === pageCount" @click="page++">下一页</button></nav>
  </section>
</template>
