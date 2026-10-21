<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { ApiError } from '@/api/http'
import { tasksApi } from '@/api/tasks'
import { systemApi, type GpuInfo } from '@/api/system'
import AlgorithmCard from '@/components/algorithms/AlgorithmCard.vue'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import AssetPicker from '@/components/assets/AssetPicker.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import TaskTable from '@/components/tasks/TaskTable.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssets } from '@/composables/useAssets'
import { useTasks } from '@/composables/useTasks'
import { toast } from '@/composables/useToast'
import type { Algorithm } from '@/types/algorithm'
import type { InferenceTask } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import { mergeParameters, parameterIssue as findParameterIssue, defaultParameters, type ParameterValues } from '@/utils/parameters'
import { nodeLabel } from '@/utils/presentation'

const MAX_BATCH = 50
const router = useRouter()
const route = useRoute()
const { availableAlgorithms, loading, error } = useAlgorithms()
const { assets, uploading, uploadProgress, error: assetError, upload } = useAssets()
const { tasks, submitting, error: taskError, create } = useTasks()
const selected = ref<Algorithm | null>(null)
const selectedAssetIds = ref<string[]>([])
const parameterValues = ref<ParameterValues>({})
const notice = ref('')
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const selectedNodeId = ref('')
const selectedGpuId = ref('')
const runtimeLoading = ref(false)
const runtimeError = ref('')
const preset = ref<InferenceTask | null>(null)
const presetLoading = ref(false)
const batchProgress = ref<{ done: number; total: number } | null>(null)
const activeSelection = computed(() => selected.value ? availableAlgorithms.value.find((item) => item.id === selected.value?.id) ?? null : availableAlgorithms.value[0] ?? null)
const selectedAssets = computed(() => selectedAssetIds.value.map((id) => assets.value.find((asset) => asset.id === id)).filter((asset) => asset !== undefined))
const primaryAsset = computed(() => selectedAssets.value[0])
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === selectedNodeId.value))
const parameterIssue = computed(() => findParameterIssue(activeSelection.value?.parameters, parameterValues.value))
const placementIssue = computed(() => {
  if (runtimeLoading.value || !selectedNodeId.value) return ''
  if (!nodes.value.some((node) => node.id === selectedNodeId.value)) return '原任务的服务器已不可用，请重新选择。'
  if (activeSelection.value?.device === 'gpu' && !nodeGpus.value.length) return '所选服务器未检测到 GPU，请刷新资源或选择其他服务器。'
  if (selectedGpuId.value && !nodeGpus.value.some((gpu) => String(gpu.index) === selectedGpuId.value)) return '所选 GPU 已不可用，请重新选择。'
  return ''
})
// 自动调度时由后端选择执行位置，不必等服务器列表加载完
const waitingForPlacement = computed(() => runtimeLoading.value && Boolean(selectedNodeId.value))
const busy = computed(() => submitting.value || batchProgress.value !== null)
const canSubmit = computed(() => Boolean(activeSelection.value && selectedAssets.value.length && !busy.value && !uploading.value && !waitingForPlacement.value && !presetLoading.value && !parameterIssue.value && !placementIssue.value))
const submitLabel = computed(() => {
  if (batchProgress.value) return `正在提交 ${batchProgress.value.done}/${batchProgress.value.total}…`
  if (submitting.value) return '正在提交任务…'
  if (presetLoading.value) return '正在读取配置…'
  if (waitingForPlacement.value) return '正在读取服务器…'
  return selectedAssets.value.length > 1 ? `批量推理 ${selectedAssets.value.length} 张图片 →` : '开始推理 →'
})

function resetParameters() {
  parameterValues.value = defaultParameters(activeSelection.value?.parameters)
}
watch(activeSelection, (algorithm) => {
  resetParameters()
  selectedGpuId.value = ''
  if (preset.value && algorithm && preset.value.algorithm_version_id === algorithm.id) {
    parameterValues.value = mergeParameters(algorithm.parameters, preset.value.parameters)
    selectedGpuId.value = preset.value.gpu_device_ids.length === 1 ? preset.value.gpu_device_ids[0]! : ''
  }
}, { immediate: true })
watch(selectedNodeId, () => { selectedGpuId.value = '' })
watch(assets, (items) => {
  // 去掉已不存在的选择；没有选择时默认使用最新上传的一张
  selectedAssetIds.value = selectedAssetIds.value.filter((id) => items.some((asset) => asset.id === id))
  if (!selectedAssetIds.value.length && items[0]) selectedAssetIds.value = [items[0].id]
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

onMounted(async () => {
  const queryAssets = typeof route.query.assets === 'string' ? route.query.assets.split(',').filter(Boolean) : []
  if (typeof route.query.asset === 'string') queryAssets.unshift(route.query.asset)
  if (queryAssets.length) selectedAssetIds.value = [...new Set(queryAssets)].slice(0, MAX_BATCH)
  if (typeof route.query.algorithm === 'string') selected.value = { id: route.query.algorithm } as Algorithm
  const fromTask = route.query.from_task
  if (typeof fromTask !== 'string') { await loadRuntimeResources(); return }
  presetLoading.value = true
  const resources = loadRuntimeResources()
  try {
    const original = await tasksApi.get(fromTask)
    await resources
    preset.value = original
    selected.value = { id: original.algorithm_version_id } as Algorithm
    if (original.asset_id) selectedAssetIds.value = [original.asset_id]
    selectedNodeId.value = original.node_id ?? ''
    await nextTick()
    if (activeSelection.value?.id === original.algorithm_version_id) parameterValues.value = mergeParameters(activeSelection.value.parameters, original.parameters)
    selectedGpuId.value = original.gpu_device_ids.length === 1 ? original.gpu_device_ids[0]! : ''
    notice.value = !activeSelection.value ? '原任务算法当前不可用，请选择可用算法后运行。' : original.gpu_device_ids.length > 1 ? '原任务使用多个 GPU，请重新确认运行资源。' : '已载入原任务配置，可调整参数后再次运行。'
  } catch (reason) {
    runtimeError.value = errorMessage(reason, '读取原任务配置失败')
  } finally { presetLoading.value = false }
})

async function handleFiles(files: File[]) {
  const outcome = await upload(files)
  if (outcome.uploaded.length) {
    selectedAssetIds.value = outcome.uploaded.map((asset) => asset.id).slice(0, MAX_BATCH)
    toast.success(outcome.uploaded.length > 1 ? `已上传 ${outcome.uploaded.length} 张图片，并全部选中` : `已上传 ${outcome.uploaded[0]!.original_name}`)
  }
}

interface FrozenRunConfig {
  algorithmVersionId: string
  parameters: ParameterValues
  nodeId?: string
  gpuDeviceIds: string[]
}

function frozenConfig(): FrozenRunConfig {
  return {
    algorithmVersionId: activeSelection.value!.id,
    parameters: { ...parameterValues.value },
    nodeId: selectedNodeId.value || undefined,
    gpuDeviceIds: selectedGpuId.value ? [selectedGpuId.value] : [],
  }
}

function payloadFor(assetId: string, config: FrozenRunConfig) {
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
    try { return await create(payloadFor(assetId, config)) } catch (reason) {
      if (!(reason instanceof ApiError && reason.status === 429) || attempt >= 30) throw reason
      taskError.value = ''
      await new Promise((resolve) => window.setTimeout(resolve, 3000))
    }
  }
}

async function submitTask() {
  if (!canSubmit.value || !activeSelection.value) return
  const ids = selectedAssets.value.map((asset) => asset.id)
  const config = frozenConfig()
  if (ids.length === 1) {
    try {
      const task = await create(payloadFor(ids[0]!, config))
      await router.push(`/tasks/${task.id}`)
    } catch { /* 保留当前输入，错误显示在表单中 */ }
    return
  }
  batchProgress.value = { done: 0, total: ids.length }
  try {
    for (const id of ids) {
      await createWithRetry(id, config)
      batchProgress.value = { done: batchProgress.value.done + 1, total: ids.length }
    }
    toast.success(`已提交 ${ids.length} 个推理任务`)
    await router.push({ path: '/tasks', query: { status: 'active' } })
  } catch (reason) {
    const done = batchProgress.value?.done ?? 0
    selectedAssetIds.value = ids.slice(done)
    const failedName = assets.value.find((asset) => asset.id === ids[done])?.original_name ?? `第 ${done + 1} 张`
    taskError.value = `已提交 ${done} 个，${failedName}失败：${errorMessage(reason, '创建任务失败')}。已保留未提交图片，可重新提交。`
  } finally { batchProgress.value = null }
}

function submitWithKeyboard(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void submitTask() }
}
</script>

<template>
  <div class="workspace" @keydown="submitWithKeyboard">
    <header class="page-heading workspace-heading">
      <div><h1>让算法开始工作</h1><p>选好算法与图片，调整参数，选择本地或远程 GPU。可以一次选多张图片批量推理。</p></div>
      <div class="toolbar-actions">
        <RouterLink v-if="availableAlgorithms.length > 1" class="secondary-button" :to="{ path: '/compare', query: primaryAsset ? { asset: primaryAsset.id } : {} }">多算法对比</RouterLink>
        <RouterLink class="secondary-button" to="/tasks">查看全部任务 →</RouterLink>
      </div>
    </header>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="build-notice" role="status">{{ notice }}</p>
    <div class="workspace-stats">
      <div><span>可用算法</span><strong>{{ availableAlgorithms.length }}<small>个版本</small></strong></div>
      <div><span>图片资源</span><strong>{{ assets.length }}<small>张图片</small></strong></div>
      <div><span>执行资源</span><strong>{{ nodes.length }}<small>台服务器 · {{ gpus.length }} 张 GPU</small></strong></div>
    </div>
    <div class="workspace-layout">
      <div class="workspace-inputs">
        <section class="section-block">
          <header class="section-heading"><div><span class="step-number">1</span><h2>选择算法</h2></div><RouterLink to="/algorithms">算法中心 →</RouterLink></header>
          <div v-if="loading" class="empty-state">正在加载算法…</div>
          <div v-else-if="!availableAlgorithms.length" class="empty-state"><strong>还没有可用算法</strong><p>先在算法中心上传算法包，构建通过后即可在这里使用。</p><RouterLink class="secondary-button" to="/algorithms">前往算法中心</RouterLink></div>
          <div v-else class="algorithm-grid">
            <AlgorithmCard v-for="algorithm in availableAlgorithms" :key="algorithm.id" :algorithm="algorithm" :selected="activeSelection?.id === algorithm.id" @select="selected = $event" />
          </div>
        </section>
        <section class="section-block workspace-image-panel">
          <header class="section-heading"><div><span class="step-number">2</span><h2>输入图片</h2><span v-if="selectedAssets.length > 1" class="count-badge">已选 {{ selectedAssets.length }} 张</span></div><RouterLink to="/assets">资源中心 →</RouterLink></header>
          <UploadDropzone :disabled="uploading" :progress="uploadProgress" accept-paste compact @files="handleFiles" />
          <p v-if="assetError" class="alert" role="alert">{{ assetError }}</p>
          <div v-if="primaryAsset && selectedAssets.length === 1" class="input-preview">
            <img class="selected-asset-preview" :src="assetsApi.thumbnailUrl(primaryAsset.id, 640)" :alt="primaryAsset.original_name" />
            <div><strong>{{ primaryAsset.original_name }}</strong><span>{{ primaryAsset.width }} × {{ primaryAsset.height }} 像素</span><span class="ready-tag">已准备好</span></div>
          </div>
          <template v-if="assets.length">
            <p class="picker-label">从已有图片中选择<small>点击选择一张；勾选右上角的圆点可多选，批量推理最多 {{ MAX_BATCH }} 张</small></p>
            <AssetPicker v-model="selectedAssetIds" :assets="assets" multiple :max="MAX_BATCH" />
          </template>
        </section>
      </div>
      <section class="section-block task-form workspace-settings">
        <header class="section-heading"><div><span class="step-number">3</span><h2>配置与运行</h2></div><button type="button" class="text-button" :disabled="!activeSelection" @click="resetParameters">恢复默认参数</button></header>
        <p class="form-intro">{{ activeSelection?.name ?? '请先选择算法' }}<span v-if="activeSelection">v{{ activeSelection.version }}</span></p>
        <ParameterForm v-if="activeSelection" v-model="parameterValues" :parameters="activeSelection.parameters" />
        <fieldset class="execution-placement"><legend>运行资源</legend>
          <label>执行服务器<select v-model="selectedNodeId" :disabled="runtimeLoading && !nodes.length"><option value="">自动调度</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option></select></label>
          <label v-if="selectedNodeId && activeSelection?.device !== 'cpu'">GPU<select v-model="selectedGpuId" :disabled="runtimeLoading"><option value="">自动分配 GPU</option><option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB</option></select></label>
          <small>{{ activeSelection?.device === 'cpu' ? '此算法使用所选服务器的 CPU。' : selectedNodeId ? '任务只在所选服务器运行。' : '平台会根据可用资源自动选择执行位置。' }}</small>
          <button type="button" class="text-button" :disabled="runtimeLoading" @click="loadRuntimeResources">{{ runtimeLoading ? '正在刷新资源…' : '刷新服务器与 GPU' }}</button>
          <p v-if="runtimeError" class="alert" role="alert">{{ runtimeError }}</p>
        </fieldset>
        <div class="submission-summary">
          <span>本次输入</span>
          <strong>{{ !primaryAsset ? '请上传或选择图片' : selectedAssets.length > 1 ? `${primaryAsset.original_name} 等 ${selectedAssets.length} 张` : primaryAsset.original_name }}</strong>
          <span>执行位置</span><strong>{{ nodeLabel(selectedNodeId, nodes) }}{{ selectedGpuId ? ` · GPU ${selectedGpuId}` : '' }}</strong>
        </div>
        <p v-if="parameterIssue || placementIssue || taskError" class="alert" role="alert">{{ parameterIssue || placementIssue || taskError }}</p>
        <button class="primary-button run-button" :disabled="!canSubmit" @click="submitTask">{{ submitLabel }}</button>
        <small class="submit-hint">{{ selectedAssets.length > 1 ? '每张图片创建一个任务，提交后在任务中心查看进度。' : '提交后自动进入任务详情，查看进度与可视化结果。' }}<kbd>Ctrl</kbd>+<kbd>Enter</kbd> 快速提交</small>
      </section>
    </div>
    <section class="section-block"><header class="section-heading"><div><h2>最近任务</h2><span class="count-badge">{{ tasks.length }}</span></div><RouterLink to="/tasks">全部任务 →</RouterLink></header><TaskTable :tasks="tasks.slice(0, 5)" :algorithms="availableAlgorithms" :assets="assets" :nodes="nodes" /></section>
  </div>
</template>
