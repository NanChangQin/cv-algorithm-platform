<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { tasksApi } from '@/api/tasks'
import { systemApi, type GpuInfo } from '@/api/system'
import AlgorithmCard from '@/components/algorithms/AlgorithmCard.vue'
import TaskTable from '@/components/tasks/TaskTable.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssets } from '@/composables/useAssets'
import { useTasks } from '@/composables/useTasks'
import type { Algorithm, ParameterSpec } from '@/types/algorithm'
import type { InferenceTask } from '@/types/task'
import { nodeLabel } from '@/utils/presentation'

const router = useRouter()
const route = useRoute()
const { availableAlgorithms, loading, error } = useAlgorithms()
const { assets, uploading, error: assetError, upload } = useAssets()
const { tasks, submitting, error: taskError, create } = useTasks()
const selected = ref<Algorithm | null>(null)
const selectedAssetId = ref('')
const parameterValues = ref<Record<string, string | number | boolean>>({})
const notice = ref('')
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const selectedNodeId = ref('')
const selectedGpuId = ref('')
const runtimeLoading = ref(false)
const runtimeError = ref('')
const preset = ref<InferenceTask | null>(null)
const presetLoading = ref(false)
const activeSelection = computed(() => selected.value ? availableAlgorithms.value.find((item) => item.id === selected.value?.id) ?? null : availableAlgorithms.value[0] ?? null)
const selectedAsset = computed(() => assets.value.find((asset) => asset.id === selectedAssetId.value))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === selectedNodeId.value))
const parameterIssue = computed(() => {
  for (const [name, spec] of Object.entries(activeSelection.value?.parameters ?? {})) {
    if (spec.type !== 'number' && spec.type !== 'integer') continue
    const value = parameterValues.value[name]
    const title = spec.title ?? name
    if (typeof value !== 'number' || !Number.isFinite(value)) return `请填写${title}`
    if (spec.type === 'integer' && !Number.isInteger(value)) return `${title}需要填写整数`
    if (spec.minimum !== undefined && value < spec.minimum) return `${title}不能小于 ${spec.minimum}`
    if (spec.maximum !== undefined && value > spec.maximum) return `${title}不能大于 ${spec.maximum}`
  }
  return ''
})
const placementIssue = computed(() => {
  if (runtimeLoading.value || !selectedNodeId.value) return ''
  if (!nodes.value.some((node) => node.id === selectedNodeId.value)) return '原任务的服务器已不可用，请重新选择。'
  if (activeSelection.value?.device === 'gpu' && !nodeGpus.value.length) return '所选服务器未检测到 GPU，请刷新资源或选择其他服务器。'
  if (selectedGpuId.value && !nodeGpus.value.some((gpu) => String(gpu.index) === selectedGpuId.value)) return '所选 GPU 已不可用，请重新选择。'
  return ''
})
const canSubmit = computed(() => Boolean(activeSelection.value && selectedAsset.value && !submitting.value && !uploading.value && !runtimeLoading.value && !presetLoading.value && !parameterIssue.value && !placementIssue.value))

function resetParameters() {
  parameterValues.value = Object.fromEntries(Object.entries(activeSelection.value?.parameters ?? {}).map(([name, spec]) => [name, spec.default]))
}
watch(activeSelection, (algorithm) => {
  resetParameters()
  selectedGpuId.value = ''
  if (preset.value && algorithm && preset.value.algorithm_version_id === algorithm.id) {
    parameterValues.value = { ...parameterValues.value, ...preset.value.parameters } as typeof parameterValues.value
    selectedGpuId.value = preset.value.gpu_device_ids.length === 1 ? preset.value.gpu_device_ids[0]! : ''
  }
}, { immediate: true })
watch(selectedNodeId, () => { selectedGpuId.value = '' })
watch(assets, (items) => {
  if (!selectedAssetId.value && items[0]) selectedAssetId.value = items[0].id
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
    runtimeError.value = reason instanceof Error ? reason.message : '运行资源加载失败'
  } finally { runtimeLoading.value = false }
}

onMounted(async () => {
  if (typeof route.query.asset === 'string') selectedAssetId.value = route.query.asset
  if (typeof route.query.algorithm === 'string') selected.value = { id: route.query.algorithm } as Algorithm
  await loadRuntimeResources()
  const fromTask = route.query.from_task
  if (typeof fromTask !== 'string') return
  presetLoading.value = true
  try {
    const original = await tasksApi.get(fromTask)
    preset.value = original
    selected.value = { id: original.algorithm_version_id } as Algorithm
    if (original.asset_id) selectedAssetId.value = original.asset_id
    selectedNodeId.value = original.node_id ?? ''
    await nextTick()
    if (activeSelection.value?.id === original.algorithm_version_id) parameterValues.value = { ...parameterValues.value, ...original.parameters } as typeof parameterValues.value
    selectedGpuId.value = original.gpu_device_ids.length === 1 ? original.gpu_device_ids[0]! : ''
    notice.value = !activeSelection.value ? '原任务算法当前不可用，请选择可用算法后运行。' : original.gpu_device_ids.length > 1 ? '原任务使用多个 GPU，请重新确认运行资源。' : '已载入原任务配置，可调整参数后再次运行。'
  } catch (reason) {
    runtimeError.value = reason instanceof Error ? reason.message : '读取原任务配置失败'
  } finally { presetLoading.value = false }
})

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (!files.length) return
  try {
    const uploaded = await upload(files)
    if (uploaded[0]) selectedAssetId.value = uploaded[0].id
  } catch { /* The upload composable exposes the error beside the input. */ }
  finally { input.value = '' }
}
function numericBounds(spec: ParameterSpec) {
  return spec.type === 'number' || spec.type === 'integer'
    ? { min: spec.minimum, max: spec.maximum, step: spec.step ?? (spec.type === 'integer' ? 1 : 0.01) } : {}
}
async function submitTask() {
  if (!canSubmit.value || !activeSelection.value) return
  try {
    const task = await create({ algorithm_version_id: activeSelection.value.id, asset_id: selectedAssetId.value, parameters: parameterValues.value,
      node_id: selectedNodeId.value || undefined, gpu_device_ids: selectedGpuId.value ? [selectedGpuId.value] : [],
    })
    await router.push(`/tasks/${task.id}`)
  } catch { /* Keep the selected input and show the API error in the form. */ }
}
</script>

<template>
  <header class="page-heading workspace-heading">
    <div><span class="eyebrow">工作台</span><h1>让算法开始工作</h1><p>选好算法与图片，调整参数，选择本地或远程 GPU。</p></div>
    <RouterLink class="secondary-button" to="/tasks">查看全部任务 →</RouterLink>
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
        <header class="section-heading"><div><span class="step-number">2</span><h2>输入图片</h2></div><RouterLink to="/assets">资源中心 →</RouterLink></header>
        <label class="upload-dropzone workspace-upload"><strong>{{ uploading ? '正在上传图片…' : '点击上传图片' }}</strong><span>支持 JPG、PNG、BMP、WebP，可一次选择多张</span><input type="file" multiple accept="image/jpeg,image/png,image/bmp,image/webp" :disabled="uploading" @change="handleUpload" /></label>
        <p v-if="assetError" class="alert" role="alert">{{ assetError }}</p>
        <label v-if="assets.length" class="field-label">或使用已有图片<select v-model="selectedAssetId"><option value="" disabled>选择图片</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.original_name }} · {{ asset.width }} × {{ asset.height }}</option></select></label>
        <div v-if="selectedAsset" class="input-preview"><img class="selected-asset-preview" :src="assetsApi.contentUrl(selectedAsset.id)" :alt="selectedAsset.original_name" /><div><strong>{{ selectedAsset.original_name }}</strong><span>{{ selectedAsset.width }} × {{ selectedAsset.height }} 像素</span><span class="ready-tag">已准备好</span></div></div>
      </section>
    </div>
    <section class="section-block task-form workspace-settings">
      <header class="section-heading"><div><span class="step-number">3</span><h2>配置与运行</h2></div><button type="button" class="text-button" :disabled="!activeSelection" @click="resetParameters">恢复默认参数</button></header>
      <p class="form-intro">{{ activeSelection?.name ?? '请先选择算法' }}<span v-if="activeSelection">v{{ activeSelection.version }}</span></p>
      <div class="parameter-fields">
        <template v-for="(spec, name) in activeSelection?.parameters ?? {}" :key="name">
          <label v-if="spec.type === 'number' || spec.type === 'integer'" class="parameter-control"><span class="parameter-title">{{ spec.title ?? name }}</span><small v-if="spec.description">{{ spec.description }}</small><div class="parameter-inputs"><input v-model.number="parameterValues[name]" type="range" v-bind="numericBounds(spec)" :aria-label="`${spec.title ?? name}滑块`" /><input v-model.number="parameterValues[name]" class="parameter-number" type="number" v-bind="numericBounds(spec)" :aria-label="spec.title ?? String(name)" /></div></label>
          <label v-else-if="spec.type === 'boolean'" class="check-control"><span>{{ spec.title ?? name }}</span><input v-model="parameterValues[name]" type="checkbox" role="switch" /></label>
          <label v-else>{{ spec.title ?? name }}<small v-if="spec.description">{{ spec.description }}</small><select v-if="spec.options" v-model="parameterValues[name]"><option v-for="option in spec.options" :key="option" :value="option">{{ option }}</option></select><input v-else v-model="parameterValues[name]" /></label>
        </template>
        <p v-if="activeSelection && !Object.keys(activeSelection.parameters).length" class="muted-copy">此算法无需额外参数。</p>
      </div>
      <fieldset class="execution-placement"><legend>运行资源</legend>
        <label>执行服务器<select v-model="selectedNodeId" :disabled="runtimeLoading"><option value="">自动调度</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option></select></label>
        <label v-if="selectedNodeId && activeSelection?.device !== 'cpu'">GPU<select v-model="selectedGpuId" :disabled="runtimeLoading"><option value="">自动分配 GPU</option><option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB</option></select></label>
        <small>{{ activeSelection?.device === 'cpu' ? '此算法使用所选服务器的 CPU。' : selectedNodeId ? '任务只在所选服务器运行。' : '平台会根据可用资源自动选择执行位置。' }}</small>
        <button type="button" class="text-button" :disabled="runtimeLoading" @click="loadRuntimeResources">{{ runtimeLoading ? '正在刷新资源…' : '刷新服务器与 GPU' }}</button>
        <p v-if="runtimeError" class="alert" role="alert">{{ runtimeError }}</p>
      </fieldset>
      <div class="submission-summary"><span>本次输入</span><strong>{{ selectedAsset?.original_name ?? '请上传或选择一张图片' }}</strong><span>执行位置</span><strong>{{ nodeLabel(selectedNodeId, nodes) }}{{ selectedGpuId ? ` · GPU ${selectedGpuId}` : '' }}</strong></div>
      <p v-if="parameterIssue || placementIssue || taskError" class="alert" role="alert">{{ parameterIssue || placementIssue || taskError }}</p>
      <button class="primary-button run-button" :disabled="!canSubmit" @click="submitTask">{{ submitting ? '正在提交任务…' : presetLoading ? '正在读取配置…' : '开始推理 →' }}</button>
      <small class="submit-hint">提交后自动进入任务详情，查看进度与可视化结果。</small>
    </section>
  </div>
  <section class="section-block"><header class="section-heading"><div><h2>最近任务</h2><span class="count-badge">{{ tasks.length }}</span></div><RouterLink to="/tasks">全部任务 →</RouterLink></header><TaskTable :tasks="tasks.slice(0, 5)" :algorithms="availableAlgorithms" :assets="assets" :nodes="nodes" /></section>
</template>
