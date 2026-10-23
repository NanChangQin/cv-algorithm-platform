<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { systemApi } from '@/api/system'
import { tasksApi } from '@/api/tasks'
import { trainingApi, type TrainingDataset } from '@/api/training'
import TaskDrawer from '@/components/tasks/TaskDrawer.vue'
import TaskTable from '@/components/tasks/TaskTable.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { TERMINAL_STATUSES } from '@/composables/useTaskWatch'
import { toast } from '@/composables/useToast'
import type { ImageAsset } from '@/types/asset'
import type { InferenceTask, TaskStatus, TaskSummary } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import { nodeLabel } from '@/utils/presentation'

const PAGE_SIZE = 20
const ACTIVE: TaskStatus[] = ['queued', 'preparing', 'starting', 'running']
const tabs: Array<{ key: string; label: string; statuses: TaskStatus[] }> = [
  { key: '', label: '全部', statuses: [] },
  { key: 'active', label: '进行中', statuses: ACTIVE },
  { key: 'completed', label: '已完成', statuses: ['completed'] },
  { key: 'failed', label: '失败', statuses: ['failed'] },
  { key: 'cancelled', label: '已取消', statuses: ['cancelled'] },
]

const route = useRoute()
const router = useRouter()
const { algorithms } = useAlgorithms()
const tasks = ref<InferenceTask[]>([])
const total = ref(0)
const summary = ref<TaskSummary | null>(null)
const assets = ref<ImageAsset[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const loading = ref(false)
const error = ref('')
const query = ref('')
const algorithmFilter = ref('')
const statusFilter = ref(typeof route.query.status === 'string' ? route.query.status : '')
const nodeFilter = ref('')
const page = ref(1)
const pendingId = ref('')
const selectedResults = ref<string[]>([])
const downloading = ref(false)
const datasets = ref<TrainingDataset[]>([])
const feedbackOpen = ref(false)
const feedbackDatasetId = ref('')
const feedbackSplit = ref<'train' | 'val' | 'test'>('train')
const feedbackMinConf = ref(0.25)
const feedbackAsBackground = ref(false)
const feedbackSubmitting = ref(false)
const downloadError = ref('')
const openTaskId = ref(typeof route.query.task === 'string' ? route.query.task : '')
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const hasFilters = computed(() => Boolean(query.value || algorithmFilter.value || statusFilter.value || nodeFilter.value))
const tabCounts = computed((): Record<string, number> => {
  const counts = summary.value?.by_status
  if (!counts) return {}
  return {
    '': summary.value!.total,
    active: ACTIVE.reduce((sum, status) => sum + (counts[status] ?? 0), 0),
    completed: counts.completed ?? 0,
    failed: counts.failed ?? 0,
    cancelled: counts.cancelled ?? 0,
  }
})

function statusesFor(key: string): TaskStatus[] {
  return tabs.find((tab) => tab.key === key)?.statuses ?? (key ? [key as TaskStatus] : [])
}

let version = 0
let disposed = false
async function load() {
  const current = ++version
  loading.value = true
  error.value = ''
  try {
    const result = await tasksApi.page({
      status: statusesFor(statusFilter.value),
      algorithm_version_id: algorithmFilter.value || undefined,
      node_id: nodeFilter.value || undefined,
      q: query.value.trim() || undefined,
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    if (current !== version) return
    total.value = result.total
    const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE))
    if (page.value > lastPage) {
      page.value = lastPage
      return
    }
    tasks.value = result.items
    void loadAssets(result.items)
  } catch (reason) {
    if (current === version) error.value = errorMessage(reason, '加载任务失败')
  } finally {
    if (current === version) loading.value = false
  }
}
async function loadSummary() {
  try { summary.value = await tasksApi.summary() } catch { /* 数量只是辅助信息 */ }
}
async function loadAssets(items: InferenceTask[]) {
  const known = new Set(assets.value.map((asset) => asset.id))
  const ids = [...new Set(items.map((task) => task.asset_id).filter((id): id is string => Boolean(id) && !known.has(id!)))]
  if (!ids.length) return
  try { assets.value = [...assets.value, ...(await assetsApi.byIds(ids))] } catch { /* 缺图片名时显示“图片” */ }
}
function refreshAll() {
  void load()
  void loadSummary()
}
function firstPageThenLoad() {
  if (page.value !== 1) page.value = 1
  else void load()
}

let searchTimer: number | undefined
watch(query, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(firstPageThenLoad, 300)
})
watch([algorithmFilter, statusFilter, nodeFilter], firstPageThenLoad)
watch(page, () => void load())
watch(openTaskId, (id) => {
  void router.replace({ query: { ...route.query, task: id || undefined } })
})

// 只刷新当前页里还没结束的任务；状态变了再更新各状态的数量
let polling = false
const poller = window.setInterval(async () => {
  if (polling || loading.value || disposed) return
  const active = tasks.value.filter((task) => !TERMINAL_STATUSES.includes(task.status)).map((task) => task.id)
  if (!active.length || document.hidden) return
  const current = version
  polling = true
  try {
    const updated = new Map((await tasksApi.byIds(active)).map((task) => [task.id, task]))
    if (disposed || current !== version) return
    let changed = false
    tasks.value = tasks.value.map((task) => {
      const next = updated.get(task.id)
      if (next && next.status !== task.status) changed = true
      return next ?? task
    })
    if (changed) {
      void loadSummary()
      if (statusesFor(statusFilter.value).length || nodeFilter.value) void load()
    }
  } catch {
    // 下一轮再试
  } finally {
    polling = false
  }
}, 2500)

onMounted(async () => {
  refreshAll()
  try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* 筛选仍可用任务记录的节点 ID */ }
})
onUnmounted(() => {
  disposed = true
  version++
  window.clearInterval(poller)
  window.clearTimeout(searchTimer)
})

function clearFilters() {
  query.value = ''
  algorithmFilter.value = ''
  statusFilter.value = ''
  nodeFilter.value = ''
}
function patchTask(updated: InferenceTask) {
  const before = tasks.value.find((task) => task.id === updated.id)
  if (!before) return
  if (before.status !== updated.status) {
    void loadSummary()
    if (statusesFor(statusFilter.value).length || nodeFilter.value) void load()
  }
  tasks.value = tasks.value.map((task) => task.id === updated.id ? updated : task)
}
function openTask(id: string) {
  openTaskId.value = id
  if (id && !tasks.value.some((task) => task.id === id)) refreshAll()
}
async function act(action: 'cancel' | 'retry', id: string) {
  if (pendingId.value) return
  pendingId.value = id
  try {
    const updated = await tasksApi[action](id)
    if (action === 'retry') {
      toast.success('已重新提交任务')
      refreshAll()
    } else {
      patchTask(updated)
      toast.info('任务已取消')
    }
  } catch (reason) {
    toast.error(errorMessage(reason, action === 'retry' ? '重试任务失败' : '取消任务失败'))
  } finally { pendingId.value = '' }
}
function toggleResult(taskId: string) {
  downloadError.value = ''
  if (selectedResults.value.includes(taskId)) selectedResults.value = selectedResults.value.filter((item) => item !== taskId)
  else if (selectedResults.value.length < 100) selectedResults.value.push(taskId)
  else downloadError.value = '一次最多归档 100 个结果。'
}
function selectPageResults() {
  const completed = tasks.value.filter((task) => task.status === 'completed').map((task) => task.id)
  selectedResults.value = [...new Set([...selectedResults.value, ...completed])].slice(0, 100)
}
async function openFeedbackPanel() {
  downloadError.value = ''
  feedbackOpen.value = true
  if (!datasets.value.length && trainingApi.datasets) {
    try {
      datasets.value = await trainingApi.datasets()
      if (!feedbackDatasetId.value && datasets.value[0]) feedbackDatasetId.value = datasets.value[0].id
    } catch (reason) {
      downloadError.value = errorMessage(reason, '加载训练数据集失败')
    }
  }
}
async function submitFeedback() {
  if (!feedbackDatasetId.value || !selectedResults.value.length || feedbackSubmitting.value) return
  feedbackSubmitting.value = true
  downloadError.value = ''
  try {
    const created = await trainingApi.datasetFeedback(feedbackDatasetId.value, {
      samples: selectedResults.value.map((task_id) => (
        feedbackAsBackground.value ? { task_id, boxes: [] } : { task_id }
      )),
      target_split: feedbackSplit.value,
      min_confidence: feedbackMinConf.value,
    })
    toast.success(`已回流生成数据集 ${created.name} v${created.version}`)
    feedbackOpen.value = false
    selectedResults.value = []
  } catch (reason) {
    downloadError.value = errorMessage(reason, '样本回流失败')
  } finally {
    feedbackSubmitting.value = false
  }
}
async function downloadArchive() {
  downloading.value = true
  downloadError.value = ''
  try {
    const blob = await tasksApi.archive(selectedResults.value)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = 'cv-results.zip'
    link.click()
    URL.revokeObjectURL(link.href)
    toast.success(`已打包 ${selectedResults.value.length} 个结果`)
  } catch (reason) {
    downloadError.value = errorMessage(reason, '结果下载失败')
  } finally { downloading.value = false }
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>任务中心</h1><p>查看推理进度和结果。点一行就能在右侧查看，不用离开列表。</p></div>
    <RouterLink class="primary-button compact-button" to="/">新建推理</RouterLink>
  </header>
  <p v-if="error || downloadError" class="alert" role="alert">{{ error || downloadError }}</p>
  <nav class="status-tabs" aria-label="按状态筛选">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      :class="{ selected: statusFilter === tab.key }"
      :aria-pressed="statusFilter === tab.key"
      @click="statusFilter = tab.key"
    >
      {{ tab.label }}<span v-if="summary" class="tab-count">{{ tabCounts[tab.key] ?? 0 }}</span>
    </button>
  </nav>
  <section class="section-block task-list-panel">
    <div class="task-filters">
      <input v-model="query" type="search" aria-label="搜索任务" placeholder="搜索任务 ID、算法、图片或服务器" />
      <select v-model="algorithmFilter" aria-label="按算法筛选">
        <option value="">全部算法</option>
        <option v-for="algorithm in algorithms" :key="algorithm.id" :value="algorithm.id">{{ algorithm.name }} v{{ algorithm.version }}</option>
      </select>
      <select v-model="nodeFilter" aria-label="按服务器筛选">
        <option value="">全部服务器</option>
        <option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option>
      </select>
      <button type="button" class="secondary-button" :disabled="loading" @click="refreshAll">{{ loading ? '刷新中…' : '刷新' }}</button>
    </div>
    <div class="toolbar">
      <span>{{ total }} 个任务</span>
      <button v-if="hasFilters" type="button" class="text-button" @click="clearFilters">清除筛选</button>
    </div>
    <div v-if="loading && !tasks.length" class="empty-state">正在加载任务…</div>
    <div v-else-if="!tasks.length" class="empty-state">
      <strong>{{ hasFilters ? '没有匹配的任务' : '还没有推理任务' }}</strong>
      <p>{{ hasFilters ? '修改或清除筛选条件后再试。' : '在工作台选择算法和图片，开始第一次推理。' }}</p>
      <button v-if="hasFilters" type="button" class="secondary-button" @click="clearFilters">清除筛选</button>
      <RouterLink v-else class="secondary-button" to="/">前往工作台</RouterLink>
    </div>
    <TaskTable
      v-else
      :tasks="tasks"
      :algorithms="algorithms"
      :assets="assets"
      :nodes="nodes"
      :pending-id="pendingId"
      :selected-results="selectedResults"
      :active-id="openTaskId"
      manageable
      @open="openTask"
      @toggle-result="toggleResult"
      @cancel="act('cancel', $event)"
      @retry="act('retry', $event)"
    />
    <div v-if="tasks.length" class="list-footer">
      <div class="toolbar-actions">
        <button type="button" class="text-button" @click="selectPageResults">选择本页已完成结果</button>
        <span v-if="selectedResults.length">已选 {{ selectedResults.length }} 项</span>
        <button v-if="selectedResults.length" type="button" class="text-button" @click="selectedResults = []">清空选择</button>
      </div>
      <div v-if="selectedResults.length" class="toolbar-actions">
        <button type="button" class="secondary-button compact-button" @click="openFeedbackPanel">回流至数据集</button>
        <button type="button" class="primary-button compact-button" :disabled="downloading" @click="downloadArchive">
          {{ downloading ? '打包中…' : '下载结果 ZIP' }}
        </button>
      </div>
    </div>
    <section v-if="feedbackOpen && selectedResults.length" class="section-block training-form" aria-label="样本回流">
      <header class="section-heading">
        <div><h2>回流样本生成新数据集版本</h2></div>
        <button type="button" class="text-button" @click="feedbackOpen = false">取消</button>
      </header>
      <div class="training-form-grid">
        <label class="field-label">目标数据集
          <select v-model="feedbackDatasetId" aria-label="回流目标数据集">
            <option value="" disabled>选择目标数据集</option>
            <option v-for="ds in datasets" :key="ds.id" :value="ds.id">{{ ds.name }} · v{{ ds.version }}（{{ ds.class_names.join(', ') }}）</option>
          </select>
        </label>
        <label class="field-label">目标划分
          <select v-model="feedbackSplit" aria-label="回流目标划分">
            <option value="train">train（训练集）</option>
            <option value="val">val（验证集）</option>
            <option value="test">test（测试集）</option>
          </select>
        </label>
        <label class="field-label">最低置信度过滤
          <input v-model.number="feedbackMinConf" type="number" min="0" max="1" step="0.05" :disabled="feedbackAsBackground" />
        </label>
        <label class="checkbox-label">
          <input v-model="feedbackAsBackground" type="checkbox" /> 作为纯背景图回流（忽略预测框，用于抑制误检）
        </label>
      </div>
      <div class="toolbar-actions">
        <button type="button" class="primary-button compact-button" :disabled="!feedbackDatasetId || feedbackSubmitting" @click="submitFeedback">
          {{ feedbackSubmitting ? '正在回流…' : `确认回流 ${selectedResults.length} 项` }}
        </button>
      </div>
    </section>
    <nav v-if="pageCount > 1" class="pagination" aria-label="任务分页">
      <button type="button" class="secondary-button" :disabled="page === 1" @click="page--">上一页</button>
      <span>{{ page }} / {{ pageCount }}</span>
      <button type="button" class="secondary-button" :disabled="page === pageCount" @click="page++">下一页</button>
    </nav>
  </section>
  <TaskDrawer
    v-if="openTaskId"
    :key="openTaskId"
    :task-id="openTaskId"
    :algorithms="algorithms"
    :nodes="nodes"
    :asset="assets.find((asset) => asset.id === tasks.find((task) => task.id === openTaskId)?.asset_id) ?? null"
    @close="openTask('')"
    @updated="patchTask"
    @open="openTask"
  />
</template>
