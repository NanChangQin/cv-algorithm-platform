<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { ApiError } from '@/api/http'
import { tasksApi } from '@/api/tasks'
import { systemApi, type GpuInfo } from '@/api/system'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import AppIcon from '@/components/layout/AppIcon.vue'
import ResultViewer from '@/components/results/ResultViewer.vue'
import TaskProgress from '@/components/tasks/TaskProgress.vue'
import AlgorithmList from '@/components/workbench/AlgorithmList.vue'
import ImageFilmstrip from '@/components/workbench/ImageFilmstrip.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssetPages } from '@/composables/useAssetPages'
import { uploadFailureMessage, uploadInBatches } from '@/composables/useAssets'
import { TERMINAL_STATUSES, useTaskWatch } from '@/composables/useTaskWatch'
import { toast } from '@/composables/useToast'
import type { ImageAsset } from '@/types/asset'
import type { CreateTaskPayload, InferenceTask, TaskStatus } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import {
  defaultParameters, mergeParameters, parameterIssue as findParameterIssue, type ParameterValues,
} from '@/utils/parameters'
import { nodeLabel, relativeTime, resultSummary } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

const MAX_BATCH = 50
const route = useRoute()
const { algorithms, availableAlgorithms, loading: algorithmsLoading, error: algorithmsError } = useAlgorithms()
const library = useAssetPages(40)

// ---- 图片：图片条里已加载的，加上链接里指定、但还没翻到的 ----
const extraAssets = ref<ImageAsset[]>([])
const knownAssets = computed(() => {
  const byId = new Map<string, ImageAsset>()
  for (const asset of [...extraAssets.value, ...library.items.value]) byId.set(asset.id, asset)
  return byId
})
const selectedAssetIds = ref<string[]>([])
const focusedAssetId = ref<string | null>(null)
const selectedAssets = computed(() => selectedAssetIds.value
  .map((id) => knownAssets.value.get(id))
  .filter((asset): asset is ImageAsset => asset !== undefined))
const focusedAsset = computed(() => (focusedAssetId.value ? knownAssets.value.get(focusedAssetId.value) : undefined)
  ?? selectedAssets.value[0] ?? null)
const focusIndex = computed(() => selectedAssets.value.findIndex((asset) => asset.id === focusedAsset.value?.id))
const uploading = ref(false)
const uploadProgress = ref<number | null>(null)
let linkedSelection = false

watch(selectedAssetIds, (ids) => {
  if (!ids.length) focusedAssetId.value = null
  else if (!focusedAssetId.value || !ids.includes(focusedAssetId.value)) focusedAssetId.value = ids[0]!
})
watch(library.items, (items, previous) => {
  // 搜索或翻页只替换图片条，已选输入仍需保留供预览和提交。
  const selected = new Set(selectedAssetIds.value)
  const retained = new Map(extraAssets.value.filter((asset) => selected.has(asset.id)).map((asset) => [asset.id, asset]))
  for (const asset of previous ?? []) if (selected.has(asset.id)) retained.set(asset.id, asset)
  extraAssets.value = [...retained.values()]
  // 没有选择时默认用最新上传的一张
  if (!linkedSelection && !selectedAssetIds.value.length && items[0]) selectedAssetIds.value = [items[0].id]
}, { immediate: true })

// ---- 算法与参数 ----
const selectedAlgorithmId = ref<string | null>(null)
const activeSelection = computed(() => availableAlgorithms.value.find((item) => item.id === selectedAlgorithmId.value)
  ?? availableAlgorithms.value[0] ?? null)
const parameterValues = ref<ParameterValues>({})
const preset = ref<InferenceTask | null>(null)
const presetLoading = ref(false)
const notice = ref('')
const parameterIssue = computed(() => findParameterIssue(activeSelection.value?.parameters, parameterValues.value))

function resetParameters() {
  parameterValues.value = defaultParameters(activeSelection.value?.parameters)
}

// ---- 运行位置 ----
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const selectedNodeId = ref('')
const selectedGpuId = ref('')
const runtimeLoading = ref(false)
const runtimeError = ref('')
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === selectedNodeId.value))
const placementIssue = computed(() => {
  if (runtimeLoading.value || !selectedNodeId.value) return ''
  if (!nodes.value.some((node) => node.id === selectedNodeId.value)) return '原任务的服务器已不可用，请重新选择。'
  if (activeSelection.value?.device === 'gpu' && !nodeGpus.value.length) return '所选服务器未检测到 GPU，请刷新资源或选择其他服务器。'
  if (selectedGpuId.value && !nodeGpus.value.some((gpu) => String(gpu.index) === selectedGpuId.value)) return '所选 GPU 已不可用，请重新选择。'
  return ''
})
// 自动调度时由后端选择执行位置，不必等服务器列表加载完
const waitingForPlacement = computed(() => runtimeLoading.value && Boolean(selectedNodeId.value))
watch(selectedNodeId, () => { selectedGpuId.value = '' })

// 换算法时恢复默认参数；“复用配置”打开时套用原任务的参数
watch(activeSelection, (algorithm) => {
  resetParameters()
  selectedGpuId.value = ''
  if (preset.value && algorithm && preset.value.algorithm_version_id === algorithm.id) {
    parameterValues.value = mergeParameters(algorithm.parameters, preset.value.parameters)
    selectedGpuId.value = preset.value.gpu_device_ids.length === 1 ? preset.value.gpu_device_ids[0]! : ''
  }
}, { immediate: true })

async function loadRuntimeResources() {
  runtimeLoading.value = true
  runtimeError.value = ''
  try {
    const resources = await systemApi.executionResources()
    nodes.value = resources.nodes
    gpus.value = resources.gpus
    if (selectedGpuId.value && !nodeGpus.value.some((gpu) => String(gpu.index) === selectedGpuId.value)) selectedGpuId.value = ''
  } catch (reason) {
    runtimeError.value = errorMessage(reason, '运行资源加载失败')
  } finally { runtimeLoading.value = false }
}

// ---- 提交 ----
interface FrozenRunConfig {
  algorithmVersionId: string
  parameters: ParameterValues
  nodeId?: string
  gpuDeviceIds: string[]
}
interface BatchRun { total: number; submitted: number; taskIds: Record<string, string> }

/** 本次打开工作台后提交的任务：`图片|算法` → 任务 ID，结果区优先显示它们 */
const runs = reactive<Record<string, string>>({})
const runKey = (assetId: string, algorithmId: string) => `${assetId}|${algorithmId}`
const submitting = ref(false)
const taskError = ref('')
const batch = ref<BatchRun | null>(null)
const batchStatuses = ref<Record<string, TaskStatus>>({})
const announced = new Set<string>()
const busy = computed(() => submitting.value)
const canSubmit = computed(() => Boolean(activeSelection.value && selectedAssets.value.length && !busy.value && !uploading.value
  && !waitingForPlacement.value && !presetLoading.value && !parameterIssue.value && !placementIssue.value))
const submitLabel = computed(() => {
  if (batch.value && batch.value.submitted < batch.value.total && submitting.value) {
    return `正在提交 ${batch.value.submitted}/${batch.value.total}…`
  }
  if (submitting.value) return '正在提交…'
  if (presetLoading.value) return '正在读取配置…'
  if (waitingForPlacement.value) return '正在读取服务器…'
  return selectedAssets.value.length > 1 ? `批量推理 ${selectedAssets.value.length} 张图片` : '运行推理'
})
const batchCounts = computed(() => {
  const statuses = Object.values(batchStatuses.value)
  return {
    completed: statuses.filter((status) => status === 'completed').length,
    failed: statuses.filter((status) => status === 'failed' || status === 'cancelled').length,
  }
})
const batchFinished = computed(() => Boolean(batch.value && batch.value.submitted === batch.value.total
  && batchCounts.value.completed + batchCounts.value.failed >= batch.value.total))

function frozenConfig(): FrozenRunConfig {
  return {
    algorithmVersionId: activeSelection.value!.id,
    parameters: { ...parameterValues.value },
    nodeId: selectedNodeId.value || undefined,
    gpuDeviceIds: selectedGpuId.value ? [selectedGpuId.value] : [],
  }
}
function payloadFor(assetId: string, config: FrozenRunConfig): CreateTaskPayload {
  return {
    algorithm_version_id: config.algorithmVersionId,
    asset_id: assetId,
    parameters: config.parameters,
    node_id: config.nodeId,
    gpu_device_ids: config.gpuDeviceIds,
  }
}

/** 平台按账号限流（默认每分钟 60 次），批量提交遇到 429 时等待后继续。 */
async function createWithRetry(assetId: string, config: FrozenRunConfig) {
  for (let attempt = 0; ; attempt++) {
    try { return await tasksApi.create(payloadFor(assetId, config)) } catch (reason) {
      if (!(reason instanceof ApiError && reason.status === 429) || attempt >= 30) throw reason
      await new Promise((resolve) => window.setTimeout(resolve, 3000))
    }
  }
}

async function submitTask() {
  if (!canSubmit.value || !activeSelection.value) return
  const ids = selectedAssets.value.map((asset) => asset.id)
  const config = frozenConfig()
  taskError.value = ''
  pinnedTaskId.value = ''
  submitting.value = true
  if (ids.length === 1) {
    batch.value = null
    try {
      const task = await tasksApi.create(payloadFor(ids[0]!, config))
      runs[runKey(ids[0]!, config.algorithmVersionId)] = task.id
      announced.add(task.id)
    } catch (reason) {
      // 保留当前输入，方便直接重试
      taskError.value = errorMessage(reason, '创建任务失败')
    } finally { submitting.value = false }
    return
  }
  batch.value = { total: ids.length, submitted: 0, taskIds: {} }
  const current = batch.value
  batchStatuses.value = {}
  try {
    for (const id of ids) {
      const task = await createWithRetry(id, config)
      runs[runKey(id, config.algorithmVersionId)] = task.id
      current.taskIds[id] = task.id
      batchStatuses.value = { ...batchStatuses.value, [id]: task.status ?? 'queued' }
      current.submitted++
    }
    toast.success(`已提交 ${ids.length} 个推理任务，点下方图片可以逐张查看结果`)
  } catch (reason) {
    const done = current.submitted
    selectedAssetIds.value = ids.slice(done)
    const failedName = knownAssets.value.get(ids[done]!)?.original_name ?? `第 ${done + 1} 张`
    taskError.value = `已提交 ${done} 个，${failedName}失败：${errorMessage(reason, '创建任务失败')}。已保留未提交的图片，可以再次提交。`
  } finally { submitting.value = false }
}

async function refreshBatch() {
  const current = batch.value
  if (!current) return
  try {
    const tasks = await tasksApi.byIds(Object.values(current.taskIds))
    const statusByTask = new Map(tasks.map((task) => [task.id, task.status]))
    const statuses: Record<string, TaskStatus> = {}
    for (const [assetId, taskId] of Object.entries(current.taskIds)) {
      const status = statusByTask.get(taskId)
      if (status) statuses[assetId] = status
    }
    if (current === batch.value) batchStatuses.value = statuses
  } catch {
    // 下一轮再刷新
  }
}
const batchTimer = window.setInterval(() => { if (batch.value && !batchFinished.value) void refreshBatch() }, 2000)

// ---- 结果区：当前图片 + 当前算法的任务 ----
const history = ref<InferenceTask[]>([])
const pinnedTaskId = ref('')
const autoTaskId = computed(() => {
  const assetId = focusedAsset.value?.id
  const algorithmId = activeSelection.value?.id
  if (!assetId || !algorithmId) return ''
  return runs[runKey(assetId, algorithmId)]
    ?? history.value.find((task) => task.algorithm_version_id === algorithmId && task.status === 'completed')?.id
    ?? ''
})
const viewedTaskId = computed(() => pinnedTaskId.value || autoTaskId.value)
const { task: viewedTask, result: viewedResult } = useTaskWatch(viewedTaskId)
const viewedAlgorithm = computed(() => algorithms.value.find((item) => item.id === viewedTask.value?.algorithm_version_id))
const showingOtherAlgorithm = computed(() => Boolean(viewedTask.value && activeSelection.value
  && viewedTask.value.algorithm_version_id !== activeSelection.value.id))
const historyOptions = computed(() => {
  const listed = [...history.value]
  if (viewedTask.value && !listed.some((task) => task.id === viewedTask.value!.id)) listed.unshift(viewedTask.value)
  return listed
})

async function loadHistory() {
  const assetId = focusedAsset.value?.id
  if (!assetId) { history.value = []; return }
  try {
    const page = await tasksApi.page({ asset_id: assetId, limit: 20 })
    if (assetId === focusedAsset.value?.id) history.value = page.items
  } catch {
    history.value = []
  }
}
function algorithmName(task: InferenceTask) {
  return algorithms.value.find((item) => item.id === task.algorithm_version_id)?.name ?? '算法'
}

watch(() => focusedAsset.value?.id, () => { pinnedTaskId.value = ''; void loadHistory() }, { immediate: true })
watch(() => activeSelection.value?.id, () => { pinnedTaskId.value = '' })
watch(() => [viewedTask.value?.id, viewedTask.value?.status] as const, ([id, status], [previousId, previous]) => {
  // 只在同一个任务结束时刷新历史，切换查看别的任务不算
  if (!id || id !== previousId || !status || status === previous || !TERMINAL_STATUSES.includes(status)) return
  void loadHistory()
  const task = viewedTask.value
  if (task && announced.delete(task.id) && status !== 'completed') toast.error(`推理${taskStatusLabel[status]}：${task.error_message ?? '请查看任务详情'}`)
})
watch(viewedResult, (result) => {
  const task = viewedTask.value
  if (result && task && announced.delete(task.id)) toast.success(`推理完成：${resultSummary(result)}`)
})

// ---- 上传 ----
async function handleFiles(files: File[]) {
  uploading.value = true
  uploadProgress.value = 0
  try {
    const outcome = await uploadInBatches(files, { onProgress: (ratio) => { uploadProgress.value = ratio } })
    if (outcome.uploaded.length) {
      library.prepend(outcome.uploaded)
      selectedAssetIds.value = outcome.uploaded.map((asset) => asset.id).slice(0, MAX_BATCH)
      focusedAssetId.value = outcome.uploaded[0]!.id
      toast.success(outcome.uploaded.length > 1 ? `已上传 ${outcome.uploaded.length} 张图片，并全部选中` : `已上传 ${outcome.uploaded[0]!.original_name}`)
    }
    if (outcome.failed.length) toast.error(uploadFailureMessage(outcome.failed))
  } finally {
    uploading.value = false
    uploadProgress.value = null
  }
}

// ---- 键盘：Ctrl+Enter 提交，←/→ 切换图片 ----
function handleKeys(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    void submitTask()
    return
  }
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  if ((event.target as HTMLElement | null)?.closest('input, select, textarea, [contenteditable="true"]')) return
  const step = event.key === 'ArrowLeft' ? -1 : 1
  if (selectedAssets.value.length > 1) {
    const next = selectedAssets.value[(focusIndex.value + step + selectedAssets.value.length) % selectedAssets.value.length]
    if (next) focusedAssetId.value = next.id
    return
  }
  const items = library.items.value
  const index = items.findIndex((asset) => asset.id === focusedAsset.value?.id)
  const next = items[index + step]
  if (next) { selectedAssetIds.value = [next.id]; focusedAssetId.value = next.id }
}

onMounted(async () => {
  window.addEventListener('keydown', handleKeys)
  const linked = typeof route.query.assets === 'string' ? route.query.assets.split(',').filter(Boolean) : []
  if (typeof route.query.asset === 'string') linked.unshift(route.query.asset)
  if (typeof route.query.algorithm === 'string') selectedAlgorithmId.value = route.query.algorithm
  const fromTask = route.query.from_task
  const resources = loadRuntimeResources()
  if (typeof fromTask === 'string') {
    presetLoading.value = true
    try {
      const original = await tasksApi.get(fromTask)
      await resources
      preset.value = original
      selectedAlgorithmId.value = original.algorithm_version_id
      if (original.asset_id) linked.unshift(original.asset_id)
      selectedNodeId.value = original.node_id ?? ''
      await nextTick()
      if (activeSelection.value?.id === original.algorithm_version_id) {
        parameterValues.value = mergeParameters(activeSelection.value.parameters, original.parameters)
      }
      selectedGpuId.value = original.gpu_device_ids.length === 1 ? original.gpu_device_ids[0]! : ''
      notice.value = !activeSelection.value || activeSelection.value.id !== original.algorithm_version_id
        ? '原任务的算法当前不可用，请选择可用算法后运行。'
        : original.gpu_device_ids.length > 1 ? '原任务使用了多个 GPU，请重新确认运行位置。' : '已载入原任务的配置，可以调整参数后再次运行。'
    } catch (reason) {
      runtimeError.value = errorMessage(reason, '读取原任务配置失败')
    } finally { presetLoading.value = false }
  }
  if (linked.length) {
    linkedSelection = true
    const ids = [...new Set(linked)].slice(0, MAX_BATCH)
    selectedAssetIds.value = ids
    try {
      const missing = ids.filter((id) => !knownAssets.value.has(id))
      if (missing.length) extraAssets.value = await assetsApi.byIds(missing)
    } catch {
      // 读不到的图片会在下面被去掉
    }
    selectedAssetIds.value = ids.filter((id) => knownAssets.value.has(id))
  }
})
onUnmounted(() => {
  window.removeEventListener('keydown', handleKeys)
  window.clearInterval(batchTimer)
})
</script>

<template>
  <div class="studio">
    <p v-if="algorithmsError" class="alert" role="alert">{{ algorithmsError }}</p>
    <p v-if="notice" class="build-notice" role="status">{{ notice }}</p>

    <div class="studio-layout">
      <aside class="studio-config" aria-label="运行配置">
        <section class="config-section">
          <header class="config-heading"><h2>算法</h2><RouterLink to="/algorithms">算法库</RouterLink></header>
          <p v-if="algorithmsLoading && !availableAlgorithms.length" class="muted-copy">正在加载算法…</p>
          <div v-else-if="!availableAlgorithms.length" class="empty-state compact">
            <strong>还没有可用算法</strong>
            <p>先在算法库导入算法包，构建通过后就能在这里使用。</p>
            <RouterLink class="secondary-button" to="/algorithms">前往算法库</RouterLink>
          </div>
          <AlgorithmList
            v-else
            :algorithms="availableAlgorithms"
            :model-value="activeSelection?.id ?? null"
            @update:model-value="selectedAlgorithmId = $event"
          />
        </section>

        <section v-if="activeSelection" class="config-section">
          <header class="config-heading">
            <h2>参数</h2>
            <button type="button" class="text-button" @click="resetParameters">恢复默认</button>
          </header>
          <ParameterForm v-model="parameterValues" :parameters="activeSelection.parameters" />
          <p v-if="!Object.keys(activeSelection.parameters).length" class="muted-copy">这个算法没有可调参数。</p>
        </section>

        <details class="config-section placement-section" :open="Boolean(selectedNodeId) || undefined">
          <summary>
            <span>运行位置</span>
            <strong>{{ nodeLabel(selectedNodeId, nodes) }}{{ selectedGpuId ? ` · GPU ${selectedGpuId}` : '' }}</strong>
          </summary>
          <fieldset class="execution-placement">
            <legend class="visually-hidden">运行位置</legend>
            <label>执行服务器
              <select v-model="selectedNodeId" :disabled="runtimeLoading && !nodes.length">
                <option value="">自动调度</option>
                <option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option>
              </select>
            </label>
            <label v-if="selectedNodeId && activeSelection?.device !== 'cpu'">GPU
              <select v-model="selectedGpuId" :disabled="runtimeLoading">
                <option value="">自动分配 GPU</option>
                <option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">
                  GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB
                </option>
              </select>
            </label>
            <small>{{ activeSelection?.device === 'cpu' ? '此算法使用所选服务器的 CPU。' : selectedNodeId ? '任务只在所选服务器运行。' : '平台会根据空闲资源自动选择执行位置。' }}</small>
            <button type="button" class="text-button" :disabled="runtimeLoading" @click="loadRuntimeResources">
              {{ runtimeLoading ? '正在刷新…' : '刷新服务器与 GPU' }}
            </button>
            <p v-if="runtimeError" class="alert" role="alert">{{ runtimeError }}</p>
          </fieldset>
        </details>

        <div class="run-bar">
          <p v-if="parameterIssue || placementIssue || taskError" class="alert" role="alert">{{ parameterIssue || placementIssue || taskError }}</p>
          <div v-if="batch" class="batch-progress" role="status">
            <div>
              <strong>批量推理 {{ batch.submitted < batch.total ? `已提交 ${batch.submitted}/${batch.total}` : `已提交 ${batch.total}/${batch.total}` }}</strong>
              <span>完成 {{ batchCounts.completed }}<template v-if="batchCounts.failed"> · 失败 {{ batchCounts.failed }}</template></span>
            </div>
            <span class="batch-bar"><i :style="{ width: `${(batchCounts.completed + batchCounts.failed) / batch.total * 100}%` }" /></span>
            <RouterLink class="text-button" :to="{ path: '/tasks', query: { status: batchFinished ? 'completed' : 'active' } }">在任务中心查看</RouterLink>
          </div>
          <button class="primary-button run-button" :disabled="!canSubmit" @click="submitTask">
            <AppIcon name="play" />{{ submitLabel }}
          </button>
          <small class="submit-hint"><kbd>Ctrl</kbd> + <kbd>Enter</kbd> 运行 · <kbd>←</kbd> <kbd>→</kbd> 切换图片</small>
        </div>
      </aside>

      <div class="studio-main">
        <ImageFilmstrip
          v-model:query="library.query.value"
          v-model:selected="selectedAssetIds"
          :focused="focusedAsset?.id ?? null"
          :items="library.items.value"
          :total="library.total.value"
          :loading="library.loading.value"
          :has-more="library.hasMore.value"
          :uploading="uploading"
          :progress="uploadProgress"
          :statuses="batch ? batchStatuses : {}"
          :max="MAX_BATCH"
          @update:focused="focusedAssetId = $event"
          @load-more="library.loadMore"
          @files="handleFiles"
        />
        <p v-if="library.error.value" class="alert" role="alert">{{ library.error.value }}</p>
        <section class="studio-viewer" aria-label="推理结果">
          <header class="viewer-header">
            <div v-if="focusedAsset" class="viewer-title">
              <strong :title="focusedAsset.original_name">{{ focusedAsset.original_name }}</strong>
              <span>{{ focusedAsset.width }} × {{ focusedAsset.height }}</span>
              <span v-if="selectedAssets.length > 1" class="count-badge">{{ focusIndex + 1 }} / {{ selectedAssets.length }}</span>
            </div>
            <div v-else class="viewer-title"><strong>结果</strong></div>
            <div class="viewer-actions">
              <label v-if="historyOptions.length" class="history-select">
                <AppIcon name="history" />
                <select :value="viewedTaskId" aria-label="这张图片的其他结果" @change="pinnedTaskId = ($event.target as HTMLSelectElement).value">
                  <option v-if="!viewedTaskId" value="">当前算法还没运行过</option>
                  <option v-for="task in historyOptions" :key="task.id" :value="task.id">
                    {{ algorithmName(task) }} · {{ taskStatusLabel[task.status] }} · {{ relativeTime(task.created_at) }}
                  </option>
                </select>
              </label>
              <RouterLink
                v-if="availableAlgorithms.length > 1 && focusedAsset"
                class="secondary-button compact-button"
                :to="{ path: '/compare', query: { asset: focusedAsset.id } }"
              >多算法对比</RouterLink>
              <a v-if="focusedAsset" class="icon-button" :href="assetsApi.contentUrl(focusedAsset.id)" target="_blank" rel="noopener" title="在新窗口查看原图" aria-label="查看原图"><AppIcon name="external" /></a>
              <RouterLink v-if="viewedTask" class="secondary-button compact-button" :to="`/tasks/${viewedTask.id}`">详情</RouterLink>
            </div>
          </header>
          <div class="viewer-body">
            <div v-if="!focusedAsset" class="viewer-empty">
              <AppIcon name="image" />
              <strong>先在上方选择或上传一张图片</strong>
              <p>支持拖入、点击选择，或直接 Ctrl+V 粘贴截图。</p>
            </div>
            <template v-else-if="viewedTask">
              <p v-if="showingOtherAlgorithm" class="viewer-note">
                正在查看 {{ viewedAlgorithm?.name ?? '其他算法' }} 的结果，运行会使用左侧选中的 {{ activeSelection?.name }}。
              </p>
              <ResultViewer
                v-if="viewedTask.status === 'completed' && viewedResult"
                compact
                :fit-offset="520"
                :result="viewedResult"
                :image-url="assetsApi.contentUrl(viewedTask.asset_id ?? focusedAsset.id)"
              />
              <div v-else class="viewer-pending">
                <img :src="assetsApi.thumbnailUrl(focusedAsset.id, 640)" :alt="focusedAsset.original_name" />
                <TaskProgress :task="viewedTask" />
              </div>
            </template>
            <div v-else class="viewer-preview">
              <img :src="assetsApi.thumbnailUrl(focusedAsset.id, 640)" :alt="focusedAsset.original_name" />
              <p class="viewer-hint">
                <template v-if="activeSelection">点“运行推理”，{{ activeSelection.name }} 的结果会直接显示在这里。</template>
                <template v-else>导入算法后即可对这张图片运行推理。</template>
              </p>
            </div>
          </div>
        </section>

      </div>
    </div>
  </div>
</template>
