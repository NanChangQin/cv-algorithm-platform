<script setup lang="ts">
import type { HistoryState } from 'vue-router'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { comparisonsApi } from '@/api/comparisons'
import { tasksApi } from '@/api/tasks'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import AssetPicker from '@/components/assets/AssetPicker.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import ClassificationRenderer from '@/components/results/ClassificationRenderer.vue'
import DetectionCanvas from '@/components/results/DetectionCanvas.vue'
import OcrRenderer from '@/components/results/OcrRenderer.vue'
import PoseRenderer from '@/components/results/PoseRenderer.vue'
import SegmentationRenderer from '@/components/results/SegmentationRenderer.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssets } from '@/composables/useAssets'
import { toast } from '@/composables/useToast'
import type { Algorithm } from '@/types/algorithm'
import type { AlgorithmComparison } from '@/types/comparison'
import type { AlgorithmResult, InferenceTask } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import { defaultParameters, parameterIssue, type ParameterValues } from '@/utils/parameters'
import { formatDateTime, formatDuration, taskTypeLabel } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

const MAX_ALGORITHMS = 8
const route = useRoute()
const router = useRouter()
const { algorithms, availableAlgorithms, loading: algorithmsLoading } = useAlgorithms()
const { assets, uploading, uploadProgress, upload } = useAssets()
const selectedAssetIds = ref<string[]>([])
const selectedAlgorithms = ref<string[]>([])
const parameterValues = ref<Record<string, ParameterValues>>({})
const expanded = ref<string[]>([])
const comparison = ref<AlgorithmComparison | null>(null)
const results = ref<Record<string, AlgorithmResult>>({})
const recent = ref<AlgorithmComparison[]>([])
const confidence = ref(0)
const overlay = ref(false)
const creating = ref(false)
const error = ref('')
const comparisonId = computed(() => String(route.params.id ?? ''))
const selectedAsset = computed(() => assets.value.find((item) => item.id === selectedAssetIds.value[0]))
const comparisonAsset = computed(() => assets.value.find((item) => item.id === comparison.value?.asset_id))
const chosen = computed(() => selectedAlgorithms.value.map((id) => availableAlgorithms.value.find((item) => item.id === id)).filter((item) => item !== undefined))
const builderIssue = computed(() => {
  if (!selectedAsset.value) return '请选择一张图片'
  if (chosen.value.length < 2) return '至少选择两个算法'
  for (const algorithm of chosen.value) {
    const issue = parameterIssue(algorithm.parameters, parameterValues.value[algorithm.id] ?? {})
    if (issue) return `${algorithm.name}：${issue}`
  }
  return ''
})
const terminal = (task: InferenceTask) => ['completed', 'failed', 'cancelled'].includes(task.status)
const allDetections = computed(() => Boolean(comparison.value?.tasks.length) && comparison.value!.tasks.every((task) => algorithmOf(task)?.task_type === 'object_detection'))

function algorithmOf(task: InferenceTask): Algorithm | undefined {
  return algorithms.value.find((item) => item.id === task.algorithm_version_id)
}
function toggleAlgorithm(algorithm: Algorithm) {
  if (selectedAlgorithms.value.includes(algorithm.id)) {
    selectedAlgorithms.value = selectedAlgorithms.value.filter((item) => item !== algorithm.id)
    return
  }
  if (selectedAlgorithms.value.length >= MAX_ALGORITHMS) { toast.info(`一次最多对比 ${MAX_ALGORITHMS} 个算法`); return }
  selectedAlgorithms.value = [...selectedAlgorithms.value, algorithm.id]
  parameterValues.value[algorithm.id] ??= defaultParameters(algorithm.parameters)
}
function toggleExpanded(id: string) {
  expanded.value = expanded.value.includes(id) ? expanded.value.filter((item) => item !== id) : [...expanded.value, id]
}

/** 汇总列中的结果数量与主要类别 */
function summarize(result: AlgorithmResult | undefined) {
  if (!result) return { count: '—', detail: '' }
  if (result.type === 'object_detection') {
    const kept = result.data.detections.filter((item) => item.score >= confidence.value)
    const counts = new Map<string, number>()
    for (const item of kept) counts.set(item.label, (counts.get(item.label) ?? 0) + 1)
    const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([label, count]) => `${label} ×${count}`)
    return { count: `${kept.length} 个目标`, detail: top.join('、') }
  }
  if (result.type === 'classification') {
    const best = [...result.data.predictions].sort((a, b) => b.score - a.score)[0]
    return { count: `${result.data.predictions.length} 个分类`, detail: best ? `${best.label} ${(best.score * 100).toFixed(1)}%` : '' }
  }
  if (result.type === 'segmentation') return { count: `${result.data.segments.length} 个区域`, detail: '' }
  if (result.type === 'ocr') return { count: `${result.data.texts.length} 段文本`, detail: result.data.texts.slice(0, 2).map((item) => item.text).join('、') }
  return { count: `${result.data.instances.length} 个姿态`, detail: '' }
}
function parameterSummary(task: InferenceTask) {
  const algorithm = algorithmOf(task)
  return Object.entries(task.parameters).map(([name, value]) => `${algorithm?.parameters[name]?.title ?? name} ${value}`).join(' · ') || '默认参数'
}
function taskDuration(task: InferenceTask) {
  if (!task.started_at || !task.completed_at) return '—'
  return formatDuration(new Date(task.completed_at).getTime() - new Date(task.started_at).getTime())
}
function recentStatus(item: AlgorithmComparison) {
  const done = item.tasks.filter((task) => task.status === 'completed').length
  if (item.tasks.some((task) => !terminal(task))) return `进行中 ${done}/${item.tasks.length}`
  return done === item.tasks.length ? '已完成' : `完成 ${done}/${item.tasks.length}`
}
function recentNames(item: AlgorithmComparison) {
  return item.tasks.map((task) => algorithmOf(task)?.name ?? '已删除的算法').join(' vs ')
}

async function handleFiles(files: File[]) {
  const outcome = await upload(files)
  if (outcome.uploaded[0]) selectedAssetIds.value = [outcome.uploaded[0].id]
  if (outcome.failed[0]) toast.error(`${outcome.failed[0].name}：${outcome.failed[0].message}`)
}

async function createComparison() {
  if (builderIssue.value || creating.value) return
  creating.value = true
  error.value = ''
  try {
    // 每个算法提交自己的一组参数（默认值或用户调整后的值），由后端按各自的 manifest 校验
    const parameters = Object.fromEntries(chosen.value.map((algorithm) => [algorithm.id, parameterValues.value[algorithm.id] ?? {}]))
    const created = await comparisonsApi.create({ asset_id: selectedAsset.value!.id, algorithm_version_ids: chosen.value.map((item) => item.id), parameters })
    comparison.value = created
    results.value = {}
    await router.push(`/compare/${created.id}`)
  } catch (reason) {
    error.value = errorMessage(reason, '创建对比失败')
  } finally { creating.value = false }
}

async function loadComparison() {
  if (!comparisonId.value) return
  try {
    const loaded = await comparisonsApi.get(comparisonId.value)
    if (loaded.id !== comparisonId.value) return
    comparison.value = loaded
    error.value = ''
    for (const task of loaded.tasks) {
      if (task.status === 'completed' && !results.value[task.id]) {
        results.value = { ...results.value, [task.id]: (await tasksApi.result(task.id)).result }
      }
    }
  } catch (reason) {
    error.value = errorMessage(reason, '读取对比结果失败')
  }
}
async function loadRecent() {
  try { recent.value = await comparisonsApi.list() } catch { recent.value = [] }
}
function prefillFromQuery() {
  if (typeof route.query.asset === 'string') selectedAssetIds.value = [route.query.asset]
  const wanted = typeof route.query.algorithms === 'string' ? route.query.algorithms.split(',') : []
  for (const id of wanted) {
    const algorithm = availableAlgorithms.value.find((item) => item.id === id)
    if (algorithm && !selectedAlgorithms.value.includes(id)) toggleAlgorithm(algorithm)
  }
  const savedParameters = window.history.state?.cvComparisonParameters as Record<string, ParameterValues> | undefined
  if (savedParameters) {
    parameterValues.value = { ...parameterValues.value, ...savedParameters }
    window.history.replaceState({ ...window.history.state, cvComparisonParameters: null }, '')
  }
}
function rerun() {
  if (!comparison.value) return
  const parameters = Object.fromEntries(comparison.value.tasks.map((task) => [task.algorithm_version_id, task.parameters]))
  void router.push({
    path: '/compare',
    query: { asset: comparison.value.asset_id, algorithms: comparison.value.tasks.map((task) => task.algorithm_version_id).join(',') },
    state: { cvComparisonParameters: JSON.parse(JSON.stringify(parameters)) as HistoryState },
  })
}

watch(availableAlgorithms, prefillFromQuery)
watch(comparisonId, (id) => {
  overlay.value = false
  if (id) { comparison.value = null; results.value = {}; void loadComparison() }
  else { selectedAlgorithms.value = []; prefillFromQuery(); void loadRecent() }
})
watch(() => route.query, () => { if (!comparisonId.value) prefillFromQuery() })
watch(assets, (items) => { if (!selectedAssetIds.value.length && items[0] && !route.query.asset) selectedAssetIds.value = [items[0].id] }, { immediate: true })
onMounted(() => {
  prefillFromQuery()
  if (comparisonId.value) void loadComparison()
  else void loadRecent()
})
const polling = window.setInterval(() => {
  if (comparison.value?.tasks.some((task) => !terminal(task))) void loadComparison()
}, 1500)
onUnmounted(() => window.clearInterval(polling))
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>结果对比</h1><p>同一张图片交给多个算法并行推理，并排比较结果数量、类别和耗时。</p></div>
    <RouterLink v-if="comparisonId" class="secondary-button" to="/compare">新建对比</RouterLink>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <template v-if="!comparisonId">
    <div v-if="!algorithmsLoading && availableAlgorithms.length < 2" class="empty-state">
      <strong>至少需要两个可用算法才能对比</strong>
      <p>当前只有 {{ availableAlgorithms.length }} 个可用算法版本。在算法中心上传并发布更多算法，或启用已停用的版本。</p>
      <RouterLink class="secondary-button" to="/algorithms">前往算法中心</RouterLink>
    </div>
    <div v-else class="compare-builder">
      <section class="section-block">
        <header class="section-heading"><div><span class="step-number">1</span><h2>选择图片</h2></div><RouterLink to="/assets">资源中心 →</RouterLink></header>
        <UploadDropzone :multiple="false" :disabled="uploading" :progress="uploadProgress" compact title="拖入一张图片，或点击选择" hint="上传后自动选中" @files="handleFiles" />
        <AssetPicker v-if="assets.length" v-model="selectedAssetIds" :assets="assets" />
      </section>
      <section class="section-block">
        <header class="section-heading"><div><span class="step-number">2</span><h2>选择算法</h2><span class="count-badge">{{ chosen.length }} / {{ MAX_ALGORITHMS }}</span></div></header>
        <div class="compare-algorithms">
          <article v-for="algorithm in availableAlgorithms" :key="algorithm.id" class="compare-algorithm" :class="{ selected: selectedAlgorithms.includes(algorithm.id) }">
            <button type="button" class="compare-algorithm-toggle" :aria-pressed="selectedAlgorithms.includes(algorithm.id)" @click="toggleAlgorithm(algorithm)">
              <span class="compare-check" aria-hidden="true">{{ selectedAlgorithms.includes(algorithm.id) ? '✓' : '' }}</span>
              <span><strong>{{ algorithm.name }}</strong><small>v{{ algorithm.version }} · {{ taskTypeLabel[algorithm.task_type] }} · {{ algorithm.device.toUpperCase() }}</small></span>
            </button>
            <template v-if="selectedAlgorithms.includes(algorithm.id) && Object.keys(algorithm.parameters).length">
              <button type="button" class="text-button" :aria-expanded="expanded.includes(algorithm.id)" @click="toggleExpanded(algorithm.id)">{{ expanded.includes(algorithm.id) ? '收起参数' : '调整参数' }}</button>
              <ParameterForm v-if="expanded.includes(algorithm.id)" class="compare-parameters" :parameters="algorithm.parameters" :model-value="parameterValues[algorithm.id] ?? {}" @update:model-value="parameterValues[algorithm.id] = $event" />
            </template>
          </article>
        </div>
        <p v-if="chosen.length > 1 && new Set(chosen.map((item) => item.task_type)).size > 1" class="muted-copy">所选算法的任务类型不同，结果会分别展示，不能叠加比较。</p>
      </section>
      <div class="compare-submit">
        <span class="muted-copy">{{ builderIssue || `将用 ${chosen.length} 个算法分析 ${selectedAsset?.original_name}` }}</span>
        <button class="primary-button compact-button" :disabled="Boolean(builderIssue) || creating" @click="createComparison">{{ creating ? '正在创建…' : `开始对比 →` }}</button>
      </div>
    </div>

    <section v-if="recent.length" class="section-block">
      <header class="section-heading"><div><h2>最近的对比</h2><span class="count-badge">{{ recent.length }}</span></div></header>
      <div class="recent-comparisons">
        <RouterLink v-for="item in recent" :key="item.id" :to="`/compare/${item.id}`" class="recent-comparison" :title="recentNames(item)">
          <img :src="assetsApi.thumbnailUrl(item.asset_id, 160)" alt="" loading="lazy" />
          <span><strong>{{ recentNames(item) }}</strong><small>{{ assets.find((asset) => asset.id === item.asset_id)?.original_name ?? '图片' }} · {{ formatDateTime(item.created_at) }}</small></span>
          <span class="count-badge">{{ recentStatus(item) }}</span>
        </RouterLink>
      </div>
    </section>
  </template>

  <template v-else-if="comparison">
    <div class="input-strip">
      <img :src="assetsApi.thumbnailUrl(comparison.asset_id, 160)" alt="" />
      <div><span>对比图片</span><strong>{{ comparisonAsset?.original_name ?? '图片' }}</strong><small>{{ formatDateTime(comparison.created_at) }} 创建 · {{ comparison.tasks.length }} 个算法</small></div>
      <button type="button" class="text-button" @click="rerun">用相同设置再建一次</button>
    </div>
    <div class="compare-summary task-table">
      <div class="task-row task-head"><span>算法</span><span>状态</span><span>结果</span><span>推理耗时</span><span>任务用时</span></div>
      <div v-for="task in comparison.tasks" :key="task.id" class="task-row">
        <RouterLink :to="`/tasks/${task.id}`" class="task-algorithm"><strong>{{ algorithmOf(task)?.name ?? '已删除的算法' }}</strong><small>{{ parameterSummary(task) }}</small></RouterLink>
        <span :class="`task-status task-${task.status}`"><i class="status-dot" />{{ taskStatusLabel[task.status] }}</span>
        <span class="task-placement"><strong>{{ summarize(results[task.id]).count }}</strong><small>{{ summarize(results[task.id]).detail }}</small></span>
        <span class="mono" data-label="推理">{{ results[task.id] ? `${results[task.id]!.timing.inference_ms.toFixed(1)} ms` : '—' }}</span>
        <span class="mono" data-label="用时">{{ taskDuration(task) }}</span>
      </div>
    </div>
    <section class="result-controls">
      <label>统一置信度过滤 <output>{{ confidence.toFixed(2) }}</output><input v-model.number="confidence" type="range" min="0" max="1" step="0.01" aria-label="统一置信度过滤" /></label>
      <label v-if="allDetections" class="check-control"><input v-model="overlay" type="checkbox" role="switch" /> 叠加视图</label>
    </section>
    <div :class="['comparison-grid', { 'overlay-mode': overlay && allDetections }]">
      <article v-for="task in comparison.tasks" :key="task.id" class="comparison-panel">
        <header>
          <div><strong>{{ algorithmOf(task)?.name ?? '已删除的算法' }}</strong><span :class="`task-status task-${task.status}`"><i class="status-dot" />{{ taskStatusLabel[task.status] }}</span></div>
          <small>{{ parameterSummary(task) }}</small>
        </header>
        <template v-if="results[task.id]">
          <DetectionCanvas
            v-if="results[task.id]!.type === 'object_detection'"
            :image-url="assetsApi.contentUrl(comparison.asset_id)"
            :result="results[task.id]! as Extract<AlgorithmResult, { type: 'object_detection' }>"
            :confidence="confidence"
            :visible-labels="[...new Set((results[task.id]! as Extract<AlgorithmResult, { type: 'object_detection' }>).data.detections.map((item) => item.label))]"
            :show-annotations="true"
          />
          <ClassificationRenderer v-else-if="results[task.id]!.type === 'classification'" :result="results[task.id]! as Extract<AlgorithmResult, { type: 'classification' }>" :confidence="confidence" />
          <SegmentationRenderer v-else-if="results[task.id]!.type === 'segmentation'" :image-url="assetsApi.contentUrl(comparison.asset_id)" :result="results[task.id]! as Extract<AlgorithmResult, { type: 'segmentation' }>" :confidence="confidence" />
          <OcrRenderer v-else-if="results[task.id]!.type === 'ocr'" :image-url="assetsApi.contentUrl(comparison.asset_id)" :result="results[task.id]! as Extract<AlgorithmResult, { type: 'ocr' }>" :confidence="confidence" />
          <PoseRenderer v-else :image-url="assetsApi.contentUrl(comparison.asset_id)" :result="results[task.id]! as Extract<AlgorithmResult, { type: 'pose_estimation' }>" :confidence="confidence" />
        </template>
        <div v-else-if="task.status === 'failed'" class="alert">{{ task.error_message ?? '推理失败' }}</div>
        <div v-else-if="task.status === 'cancelled'" class="empty-state">任务已取消</div>
        <div v-else class="empty-state">{{ task.status === 'completed' ? '正在读取结果…' : `${taskStatusLabel[task.status]}…` }}</div>
        <footer>
          <span>{{ summarize(results[task.id]).count }}</span>
          <RouterLink class="text-button" :to="`/tasks/${task.id}`">任务详情 →</RouterLink>
        </footer>
      </article>
    </div>
  </template>
  <div v-else class="empty-state">正在加载对比…</div>
</template>
