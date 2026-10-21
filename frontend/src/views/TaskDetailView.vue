<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { assetsApi } from '@/api/assets'
import { tasksApi } from '@/api/tasks'
import { systemApi } from '@/api/system'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { toast } from '@/composables/useToast'
import DetectionCanvas from '@/components/results/DetectionCanvas.vue'
import ClassificationRenderer from '@/components/results/ClassificationRenderer.vue'
import OcrRenderer from '@/components/results/OcrRenderer.vue'
import PoseRenderer from '@/components/results/PoseRenderer.vue'
import SegmentationRenderer from '@/components/results/SegmentationRenderer.vue'
import type { ImageAsset } from '@/types/asset'
import type { AlgorithmResult, Detection, InferenceTask } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import { taskStatusLabel } from '@/utils/status'
import { formatDuration, nodeLabel, taskElapsed } from '@/utils/presentation'

const route = useRoute()
const router = useRouter()
const { algorithms, availableAlgorithms } = useAlgorithms()
const task = ref<InferenceTask | null>(null)
const result = ref<AlgorithmResult | null>(null)
const asset = ref<ImageAsset | null>(null)
const nodes = ref<Array<{ id: string; name: string }>>([])
const error = ref('')
const pending = ref(false)
const confidence = ref(0)
const showAnnotations = ref(true)
const hiddenLabels = ref<string[]>([])
const highlighted = ref<Detection | null>(null)
const showAllDetections = ref(false)
const now = ref(Date.now())
const taskId = computed(() => String(route.params.id))
const algorithm = computed(() => algorithms.value.find((item) => item.id === task.value?.algorithm_version_id))
const algorithmName = computed(() => algorithm.value?.name ?? '算法推理任务')
const terminal = computed(() => Boolean(task.value && ['completed', 'failed', 'cancelled'].includes(task.value.status)))
const labelCounts = computed(() => {
  const counts = new Map<string, number>()
  if (result.value?.type === 'object_detection') {
    for (const item of result.value.data.detections) if (item.score >= confidence.value) counts.set(item.label, (counts.get(item.label) ?? 0) + 1)
  }
  return counts
})
const labels = computed(() => result.value?.type === 'object_detection' ? [...new Set(result.value.data.detections.map((item) => item.label))].sort() : [])
const visibleLabels = computed(() => labels.value.filter((label) => !hiddenLabels.value.includes(label)))
const visibleDetections = computed(() => result.value?.type === 'object_detection'
  ? result.value.data.detections.filter((item) => item.score >= confidence.value && !hiddenLabels.value.includes(item.label)).sort((a, b) => b.score - a.score)
  : [])
const listedDetections = computed(() => showAllDetections.value ? visibleDetections.value : visibleDetections.value.slice(0, 20))
const imageUrl = computed(() => task.value?.asset_id ? assetsApi.contentUrl(task.value.asset_id) : '')
const resultCount = computed(() => {
  const value = result.value
  if (!value) return ''
  if (value.type === 'object_detection') return `${visibleDetections.value.length} 个目标`
  if (value.type === 'classification') return `${value.data.predictions.length} 个分类`
  if (value.type === 'segmentation') return `${value.data.segments.length} 个分割区域`
  if (value.type === 'ocr') return `${value.data.texts.length} 段文本`
  return `${value.data.instances.length} 个姿态`
})
const runningFor = computed(() => {
  if (!task.value?.started_at || terminal.value) return taskElapsed(task.value ?? ({} as InferenceTask))
  return formatDuration(now.value - new Date(task.value.started_at).getTime())
})
const progressMessage: Record<string, string> = {
  queued: '任务已进入队列，正在等待执行服务领取。', preparing: '正在校验输入图片与算法配置。',
  starting: '正在准备算法实例，首次运行可能需要加载模型。', running: '算法正在推理，完成后会自动展示结果。',
  cancelled: '任务已取消，可以复用配置再次运行。',
}
const stages = ['queued', 'preparing', 'starting', 'running', 'completed']
let eventSource: EventSource | null = null
let loadingId = ''
let disposed = false

async function load() {
  const id = taskId.value
  if (loadingId === id || disposed) return
  loadingId = id
  try {
    const loaded = await tasksApi.get(id)
    if (disposed || id !== taskId.value) return
    task.value = loaded
    error.value = ''
    if (loaded.asset_id && asset.value?.id !== loaded.asset_id) {
      assetsApi.get(loaded.asset_id).then((item) => { if (!disposed) asset.value = item }).catch(() => { /* 原图信息只是补充展示 */ })
    }
    if (loaded.status === 'completed' && !result.value) {
      const response = await tasksApi.result(id)
      if (!disposed && id === taskId.value) result.value = response.result
    }
    if (terminal.value) { eventSource?.close(); eventSource = null }
  } catch (reason) {
    if (id === taskId.value) error.value = errorMessage(reason, '加载任务失败')
  } finally { if (loadingId === id) loadingId = '' }
}
async function openTask() {
  const id = taskId.value
  eventSource?.close(); eventSource = null
  task.value = null; result.value = null; asset.value = null; hiddenLabels.value = []; confidence.value = 0; error.value = ''; highlighted.value = null
  await load()
  if (id !== taskId.value) return
  if (!disposed && task.value && !terminal.value) eventSource = tasksApi.subscribe(taskId.value, () => void load())
}
onMounted(async () => {
  void openTask()
  try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* 资源发现离线时仍显示记录的节点 ID */ }
})
watch(taskId, () => void openTask())
const polling = window.setInterval(() => { if (!terminal.value || (task.value?.status === 'completed' && !result.value)) void load() }, 2000)
const ticker = window.setInterval(() => { if (!terminal.value) now.value = Date.now() }, 1000)
onUnmounted(() => { disposed = true; window.clearInterval(polling); window.clearInterval(ticker); eventSource?.close() })

function toggleLabel(label: string) { hiddenLabels.value = hiddenLabels.value.includes(label) ? hiddenLabels.value.filter((item) => item !== label) : [...hiddenLabels.value, label] }
function downloadJson() {
  if (!result.value) return
  const link = document.createElement('a')
  link.download = `result-${taskId.value}.json`; link.href = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' })); link.click(); URL.revokeObjectURL(link.href)
}
async function copyId() {
  try { await navigator.clipboard.writeText(taskId.value); toast.success('已复制任务 ID') } catch { toast.info(taskId.value) }
}
async function act(action: 'cancel' | 'retry') {
  if (pending.value) return
  pending.value = true
  try {
    const updated = await tasksApi[action](taskId.value)
    if (action === 'retry') { toast.success('已重新提交任务'); await router.push(`/tasks/${updated.id}`) }
    else { task.value = updated; eventSource?.close(); eventSource = null; toast.info('任务已取消') }
  } catch (reason) { error.value = errorMessage(reason, '操作失败，请重试') }
  finally { pending.value = false }
}
function boxSize(detection: Detection) {
  const [x1, y1, x2, y2] = detection.bbox
  return `${Math.round(x2 - x1)} × ${Math.round(y2 - y1)}`
}
</script>

<template>
  <RouterLink class="back-link" to="/tasks">← 返回任务中心</RouterLink>
  <header class="page-heading page-heading-actions">
    <div>
      <h1>{{ task?.status === 'completed' ? '推理结果' : '任务详情' }}</h1>
      <p>{{ algorithmName }}<button type="button" class="task-short-id mono copy-id" title="复制完整任务 ID" @click="copyId">#{{ taskId.slice(0, 8) }}</button></p>
    </div>
    <div class="toolbar-actions">
      <button v-if="result" class="secondary-button" @click="downloadJson">下载 JSON</button>
      <RouterLink v-if="task?.asset_id && availableAlgorithms.length > 1" class="secondary-button" :to="{ path: '/compare', query: { asset: task.asset_id, algorithms: task.algorithm_version_id } }">对比其他算法</RouterLink>
      <button v-if="task && !terminal" class="secondary-button danger-button" :disabled="pending" @click="act('cancel')">取消任务</button>
      <button v-if="task && ['failed', 'cancelled'].includes(task.status)" class="secondary-button" :disabled="pending" @click="act('retry')">重试任务</button>
      <RouterLink v-if="task" class="primary-button compact-button" :to="{ path: '/', query: { from_task: task.id } }">复用配置 →</RouterLink>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }} <button class="text-button" @click="load">重新加载</button></p>
  <div v-if="!task" class="empty-state">{{ error ? '暂时无法读取任务，请重试。' : '正在加载任务…' }}</div>
  <template v-else>
    <section class="task-summary">
      <div><span>任务状态</span><strong :class="`task-status task-${task.status}`"><i class="status-dot" />{{ taskStatusLabel[task.status] }}</strong></div>
      <div><span>执行服务器</span><strong>{{ task.status === 'completed' && !task.execution_node_id && !task.node_id ? '未记录位置' : nodeLabel(task.execution_node_id ?? task.node_id, nodes) }}</strong></div>
      <div><span>执行设备</span><strong>{{ task.execution_node_id ? (task.execution_gpu_device_ids.length ? `GPU ${task.execution_gpu_device_ids.join(', ')}` : 'CPU') : task.status === 'completed' ? '未记录设备' : (task.gpu_device_ids.length ? `GPU ${task.gpu_device_ids.join(', ')}` : '自动分配') }}</strong></div>
      <div><span>任务用时</span><strong>{{ runningFor }}</strong></div>
    </section>
    <div v-if="asset" class="input-strip">
      <img :src="assetsApi.thumbnailUrl(asset.id, 160)" :alt="asset.original_name" />
      <div><span>输入图片</span><strong>{{ asset.original_name }}</strong><small>{{ asset.width }} × {{ asset.height }} 像素</small></div>
      <a class="text-button" :href="assetsApi.contentUrl(asset.id)" target="_blank" rel="noopener">查看原图</a>
      <RouterLink class="text-button" :to="{ path: '/', query: { asset: asset.id } }">用这张图再推理</RouterLink>
    </div>
    <div v-if="task.status === 'failed'" class="alert" role="alert"><strong>推理未完成</strong><p>{{ task.error_message ?? '执行失败，请检查算法和运行资源后重试。' }}</p><small>{{ task.error_code }}</small></div>
    <section v-else-if="task.status !== 'completed'" class="section-block task-progress-panel"><p role="status">{{ progressMessage[task.status] }}</p><ol v-if="!terminal" class="task-progress"><li v-for="(stage, index) in stages" :key="stage" :class="{ done: index < stages.indexOf(task.status), current: index === stages.indexOf(task.status) }"><span>{{ index + 1 }}</span>{{ taskStatusLabel[stage as keyof typeof taskStatusLabel] }}</li></ol></section>
    <template v-else-if="result">
      <section class="result-section">
        <header class="section-heading"><div><h2>可视化结果</h2><span class="count-badge">{{ resultCount }}</span></div><span class="muted-copy">{{ result.input.width }} × {{ result.input.height }}</span></header>
        <section class="result-controls"><label>最低置信度 <output>{{ confidence.toFixed(2) }}</output><input v-model.number="confidence" aria-label="结果最低置信度" type="range" min="0" max="1" step="0.01" /></label><label class="check-control"><input v-model="showAnnotations" type="checkbox" role="switch" /> 显示结果图层</label></section>
        <div v-if="labels.length" class="filter-chips"><span>显示类别</span><button v-for="label in labels" :key="label" :class="{ disabled: hiddenLabels.includes(label) }" :aria-pressed="!hiddenLabels.includes(label)" @click="toggleLabel(label)">{{ label }}<small>{{ labelCounts.get(label) ?? 0 }}</small></button><button v-if="hiddenLabels.length" class="text-button" @click="hiddenLabels = []">恢复全部</button></div>
        <div v-if="result.type === 'object_detection'" class="detection-layout">
          <DetectionCanvas :image-url="imageUrl" :result="result" :confidence="confidence" :visible-labels="visibleLabels" :show-annotations="showAnnotations" :highlight="highlighted" @hover="highlighted = $event" />
          <aside class="detection-list" aria-label="检测结果列表">
            <header><strong>检测结果</strong><span>{{ visibleDetections.length }} 个</span></header>
            <p v-if="!visibleDetections.length" class="muted-copy">没有达到当前置信度的目标，可以调低“最低置信度”。</p>
            <table v-else>
              <thead><tr><th>类别</th><th>置信度</th><th>尺寸</th></tr></thead>
              <tbody>
                <tr v-for="(detection, index) in listedDetections" :key="index" :class="{ active: highlighted === detection }" tabindex="0" @mouseenter="highlighted = detection" @mouseleave="highlighted = null" @focus="highlighted = detection" @blur="highlighted = null">
                  <td>{{ detection.label }}</td>
                  <td><span class="score-bar"><i :style="{ width: `${detection.score * 100}%` }" /></span>{{ (detection.score * 100).toFixed(1) }}%</td>
                  <td class="mono">{{ boxSize(detection) }}</td>
                </tr>
              </tbody>
            </table>
            <button v-if="visibleDetections.length > listedDetections.length" type="button" class="text-button" @click="showAllDetections = true">显示全部 {{ visibleDetections.length }} 个</button>
          </aside>
        </div>
        <ClassificationRenderer v-else-if="result.type === 'classification'" :result="result" :confidence="confidence" />
        <SegmentationRenderer v-else-if="result.type === 'segmentation'" :image-url="imageUrl" :result="result" :confidence="confidence" />
        <OcrRenderer v-else-if="result.type === 'ocr'" :image-url="imageUrl" :result="result" :confidence="confidence" />
        <PoseRenderer v-else-if="result.type === 'pose_estimation'" :image-url="imageUrl" :result="result" :confidence="confidence" />
      </section>
      <section class="timing-grid"><div><span>预处理</span><strong>{{ result.timing.preprocess_ms.toFixed(1) }} ms</strong></div><div><span>模型推理</span><strong>{{ result.timing.inference_ms.toFixed(1) }} ms</strong></div><div><span>后处理</span><strong>{{ result.timing.postprocess_ms.toFixed(1) }} ms</strong></div><div><span>结果数量</span><strong>{{ resultCount }}</strong></div></section>
    </template>
    <div v-else class="empty-state">正在读取推理结果…</div>
    <details class="section-block task-diagnostics"><summary>参数与执行信息</summary><dl><div><dt>完整任务 ID</dt><dd class="mono">{{ task.id }}</dd></div><div><dt>算法版本</dt><dd>{{ algorithmName }}<template v-if="algorithm"> · v{{ algorithm.version }}</template></dd></div><div><dt>算法容器</dt><dd class="mono">{{ task.container_id ?? '尚未分配' }}</dd></div><div><dt>创建时间</dt><dd>{{ new Date(task.created_at).toLocaleString('zh-CN') }}</dd></div><div v-if="task.retry_of"><dt>重试自</dt><dd><RouterLink class="text-button mono" :to="`/tasks/${task.retry_of}`">#{{ task.retry_of.slice(0, 8) }}</RouterLink></dd></div></dl><h3>运行参数</h3><pre>{{ JSON.stringify(task.parameters, null, 2) }}</pre></details>
  </template>
</template>
