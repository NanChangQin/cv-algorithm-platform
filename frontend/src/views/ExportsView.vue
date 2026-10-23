<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { exportsApi, type ExportJob, type ExportReport } from '@/api/exports'
import { systemApi } from '@/api/system'
import { activeTrainingStatuses, trainingApi, trainingStatusLabel, type TrainingDataset, type TrainingModel, type TrainingSourceOption } from '@/api/training'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { toast } from '@/composables/useToast'
import type { ParameterContract } from '@/types/algorithm'
import { errorMessage } from '@/utils/errors'
import { parameterIssue, type ParameterValues } from '@/utils/parameters'
import { formatBytes, formatDateTime } from '@/utils/presentation'

const route = useRoute()
const router = useRouter()
const jobs = shallowRef<ExportJob[]>([])
const current = shallowRef<ExportJob | null>(null)
const report = shallowRef<ExportReport | null>(null)
const logs = ref<string[]>([])
const datasets = shallowRef<TrainingDataset[]>([])
const models = shallowRef<TrainingModel[]>([])
const sources = shallowRef<TrainingSourceOption[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const contract = shallowRef<ParameterContract | null>(null)
const validationContract = shallowRef<ParameterContract | null>(null)
const error = ref('')
const name = ref('')
const datasetId = ref('')
const nodeId = ref('')
const model = ref('yolo11n.pt')
const sourceKind = ref('builtin')
const sourceKey = ref('')
const parameters = shallowRef<ParameterValues>({ format: 'onnx', imgsz: 640, batch: 1, dynamic: false, simplify: true, opset: 17 })
const validation = shallowRef<ParameterValues>({ split: 'val', conf: 0.001, iou: 0.7, plots: false, workers: 0 })
const maxDrop = ref(0.01)
const warmup = ref(1)
const iterations = ref(5)
const threads = ref(2)
const publishKey = ref('')
const publishName = ref('')
const publishVersion = ref('')
const publishing = ref(false)
const submitting = ref(false)
const uploadProgress = ref<number | null>(null)
const exportIssue = ref('')
const validationIssue = ref('')
const controlled = new Set(['batch', 'imgsz', 'rect', 'quantize', 'fraction', 'compile', 'augment', 'channels_last', 'nms'])
const specs = computed(() => Object.fromEntries(Object.entries(contract.value?.parameters ?? {}).filter(([, spec]) => spec.status === 'configurable')))
const validationSpecs = computed(() => Object.fromEntries(Object.entries(validationContract.value?.parameters ?? {}).filter(([key, spec]) => spec.status === 'configurable' && !controlled.has(key))))
const restricted = computed(() => Object.entries(contract.value?.parameters ?? {}).filter(([, spec]) => spec.status !== 'configurable'))
const selectedSource = computed(() => sources.value.find((item) => sourceId(item) === sourceKey.value))
const selectedDataset = computed(() => datasets.value.find((item) => item.id === datasetId.value))
const active = (job: ExportJob) => activeTrainingStatuses.includes(job.status)
const selectedId = computed(() => String(route.params.id ?? ''))
const phases: Record<string, string> = { converting: '转换 ONNX', smoke_test: 'ONNX Runtime 冒烟推理', validating_reference: '验证原始权重', validating_onnx: '验证 ONNX', benchmarking: '测量推理耗时', quality_gate_failed: '精度检查未通过', completed: '完成' }
const metricKeys = computed(() => Object.keys(report.value?.reference_metrics ?? {}).filter((key) => key !== 'fitness'))
const formIssue = computed(() => {
  if (!contract.value || !validationContract.value) return '正在加载参数契约'
  if (!nodeId.value || !datasetId.value) return '请选择服务器和验证数据版本'
  if (sourceKind.value === 'managed' && !selectedSource.value) return '请选择来源权重'
  const split = String(validation.value.split ?? 'val') as 'train' | 'val' | 'test'
  const count = selectedDataset.value?.stats.splits[split]?.images ?? 0
  if (!count) return `数据集没有 ${split} 图片`
  const batch = Number(parameters.value.batch ?? 1)
  if (!parameters.value.dynamic && Array.isArray(parameters.value.imgsz) && new Set(parameters.value.imgsz).size > 1) return '静态 ONNX 要求正方形尺寸；矩形请选择动态导出'
  if (!parameters.value.dynamic && count % batch) return '静态 ONNX 的验证图片数须整除 batch；请选择动态导出或 batch=1'
  if (!Number.isFinite(maxDrop.value) || maxDrop.value < 0 || maxDrop.value > 1) return '允许精度下降范围为 0～1'
  if (![warmup.value, iterations.value, threads.value].every(Number.isInteger) || warmup.value < 0 || warmup.value > 20 || iterations.value < 1 || iterations.value > 100 || threads.value < 1 || threads.value > 32) return '检查预热次数、测量次数和线程数'
  return exportIssue.value || validationIssue.value || parameterIssue(specs.value, parameters.value) || parameterIssue(validationSpecs.value, validation.value)
})
function sourceId(item: TrainingSourceOption) { return `${item.source.kind}:${item.source.reference}:${item.source.checkpoint ?? 'best'}` }
function status(job: ExportJob) { return job.status === 'running' ? '执行中' : trainingStatusLabel[job.status] }
function metric(value: number | undefined) { return value === undefined ? '—' : (value * 100).toFixed(3) }
async function loadSelected() {
  const id = selectedId.value
  if (!id) { current.value = null; report.value = null; logs.value = []; return }
  const job = await exportsApi.get(id)
  if (selectedId.value !== id) return
  current.value = job
  const output = await exportsApi.logs(id)
  if (selectedId.value !== id) return
  logs.value = output
  if (job.artifacts['export-report.json'] !== undefined) {
    const result = await exportsApi.report(id)
    if (selectedId.value === id) report.value = result
  }
}
async function refresh() { jobs.value = await exportsApi.list(); await loadSelected() }
async function upload(files: File[]) {
  if (!files[0]) return
  uploadProgress.value = 0
  try {
    const weight = await trainingApi.uploadWeight(files[0], (ratio) => { uploadProgress.value = ratio })
    sources.value = await trainingApi.sources()
    sourceKind.value = 'managed'
    sourceKey.value = `upload:${weight.id}:best`
  } catch (reason) { toast.error(errorMessage(reason, '上传失败')) }
  finally { uploadProgress.value = null }
}
async function create() {
  if (formIssue.value || submitting.value || uploadProgress.value !== null) return
  submitting.value = true
  try {
    const job = await exportsApi.create({ name: name.value.trim(), dataset_id: datasetId.value, node_id: nodeId.value, model: model.value,
      source: sourceKind.value === 'builtin' ? { kind: 'builtin' } : selectedSource.value!.source,
      parameters: parameters.value,
      verification: { parameters: validation.value, max_map_drop: maxDrop.value, warmup: warmup.value, iterations: iterations.value, threads: threads.value },
    })
    await router.push(`/exports/${job.id}`)
    jobs.value = await exportsApi.list()
    toast.success('导出任务已创建')
  } catch (reason) { toast.error(errorMessage(reason, '创建导出失败')) }
  finally { submitting.value = false }
}
async function publishModel() {
  if (!current.value || publishing.value) return
  publishing.value = true
  try {
    const model = await exportsApi.publish(current.value.id, { key: publishKey.value.trim(), name: publishName.value.trim(), version: publishVersion.value.trim() })
    toast.success('已发布为独立 ONNX 推理版本')
    await router.push({ path: '/deployments', query: { algorithm: model.id } })
  } catch (reason) { toast.error(errorMessage(reason, '发布失败')) }
  finally { publishing.value = false }
}
async function stop() {
  if (!current.value) return
  try { await exportsApi.stop(current.value.id); await refresh() }
  catch (reason) { toast.error(errorMessage(reason, '停止失败')) }
}
async function remove() {
  if (!current.value || !window.confirm('删除导出任务及其产物？已下载的文件不受影响。')) return
  try { await exportsApi.remove(current.value.id); await router.push('/exports'); await refresh() }
  catch (reason) { toast.error(errorMessage(reason, '删除失败')) }
}
let timer: number | undefined
let disposed = false
function schedule() {
  if (disposed) return
  timer = window.setTimeout(async () => {
    try { if (jobs.value.some(active) || (current.value && active(current.value))) await refresh() }
    catch (reason) { error.value = errorMessage(reason, '刷新导出失败') }
    schedule()
  }, 3000)
}
onMounted(async () => {
  try {
    const [list, data, baseModels, origins, resources, exportContract, valContract] = await Promise.all([
      exportsApi.list(), trainingApi.datasets(), trainingApi.models(), trainingApi.sources(), systemApi.executionResources(),
      trainingApi.parameterContract('export'), trainingApi.parameterContract('val'),
    ])
    jobs.value = list; datasets.value = data; models.value = baseModels; sources.value = origins; nodes.value = resources.nodes
    contract.value = exportContract; validationContract.value = valContract
    datasetId.value = data[0]?.id ?? ''; nodeId.value = nodes.value[0]?.id ?? ''
    const origin = origins.find((item) => route.query.run ? item.source.kind === 'run' && item.source.reference === route.query.run && item.source.checkpoint === (route.query.checkpoint ?? 'best') : route.query.model ? item.source.kind === 'algorithm' && item.source.reference === route.query.model : false)
    if (origin) { sourceKind.value = 'managed'; sourceKey.value = sourceId(origin) }
    await loadSelected()
  } catch (reason) { error.value = errorMessage(reason, '加载导出中心失败') }
  schedule()
})
watch(selectedId, async () => {
  report.value = null; logs.value = []; current.value = null
  try { await loadSelected() } catch (reason) { error.value = errorMessage(reason, '加载导出任务失败') }
})
onBeforeUnmount(() => { disposed = true; window.clearTimeout(timer) })
</script>

<template>
  <div class="page-stack">
    <header class="page-heading"><div><p class="eyebrow">模型生命周期</p><h1>模型导出</h1><p>转换为 FP32 ONNX，完成目标运行时验证、同集精度对比和 CPU 推理耗时测量。</p></div></header>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <section class="section-block">
      <header class="section-heading"><h2>新建导出</h2></header>
      <form class="stacked-form" @submit.prevent="create">
        <div class="form-grid">
          <label>实验名称<input v-model="name" maxlength="120" placeholder="ONNX 导出与验证" /></label>
          <label>执行服务器（CPU）<select v-model="nodeId"><option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.name }}</option></select></label>
          <label>验证数据版本<select v-model="datasetId"><option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · v{{ dataset.version }}</option></select></label>
          <label>模型来源<select v-model="sourceKind"><option value="builtin">内置模型</option><option value="managed">上传 / 发布模型 / 历史权重</option></select></label>
          <label v-if="sourceKind === 'builtin'">预置权重<select v-model="model"><option v-for="item in models" :key="item.key" :value="item.key">{{ item.label }}</option></select></label>
          <label v-else>来源权重<select v-model="sourceKey"><option value="">选择来源</option><option v-for="item in sources" :key="sourceId(item)" :value="sourceId(item)">{{ item.label }}</option></select></label>
        </div>
        <UploadDropzone compact accept=".pt" :multiple="false" :progress="uploadProgress" :disabled="uploadProgress !== null" title="上传要导出的 .pt 权重" hint="单个 Ultralytics 目标检测 .pt 权重，最多 2 GiB" @files="upload" />
        <h3>导出参数</h3>
        <ParameterForm v-if="contract" v-model="parameters" :parameters="specs" @validity="exportIssue = $event" />
        <details><summary>平台管理及暂不支持的参数</summary><dl class="definition-list"><template v-for="[key, spec] in restricted" :key="key"><dt>{{ key }}</dt><dd>{{ spec.reason }}</dd></template></dl></details>
        <h3>同集验证配置</h3>
        <p class="muted-copy">两种模型使用同一数据版本及验证参数。batch、imgsz 取导出值，rect=false、fraction=1、FP32；关闭测试时增强和图编译，nms=false。数据集类别名称及顺序须与模型一致。</p>
        <ParameterForm v-if="validationContract" v-model="validation" :parameters="validationSpecs" @validity="validationIssue = $event" />
        <div class="form-grid">
          <label>允许 mAP50-95 绝对下降<input v-model.number="maxDrop" type="number" min="0" max="1" step="0.001" /><small>0.01 表示允许下降 1 个百分点；超过后任务失败，报告和产物保留。</small></label>
          <label>预热次数<input v-model.number="warmup" type="number" min="0" max="20" /></label>
          <label>测量次数<input v-model.number="iterations" type="number" min="1" max="100" /></label>
          <label>CPU 线程数<input v-model.number="threads" type="number" min="1" max="32" /></label>
        </div>
        <p v-if="formIssue" class="muted-copy">{{ formIssue }}</p>
        <button class="primary-button" :disabled="Boolean(formIssue) || submitting || uploadProgress !== null">{{ submitting ? '提交中…' : '导出并验证' }}</button>
      </form>
    </section>
    <section v-if="current" class="section-block">
      <header class="section-heading"><div><h2>{{ current.name }}</h2><p>{{ status(current) }} · {{ current.stage || phases[current.progress?.phase ?? ''] || current.progress?.phase }}</p></div><div class="inline-actions"><button v-if="active(current)" class="secondary-button" @click="stop">停止导出</button><button v-else class="secondary-button" @click="remove">删除任务</button></div></header>
      <p>{{ current.progress?.source?.label ?? current.model }} · {{ formatDateTime(current.created_at) }}</p>
      <p v-if="current.error" class="error-banner">{{ current.error }}</p>
      <template v-if="report">
        <p class="build-notice">精度检查{{ report.quality_gate.passed ? '通过' : '未通过' }} · mAP50-95 下降 {{ metric(report.quality_gate.map50_95_drop) }} 个百分点 · 允许 {{ metric(report.quality_gate.max_map_drop) }}</p>
        <p>{{ report.format }} / {{ report.precision }} · 输入 {{ report.input_shape.join(' × ') }} · {{ report.dynamic ? '动态形状' : '固定形状' }} · opset {{ report.opset }} · {{ report.split }}</p>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>指标（%）</th><th>原始权重</th><th>ONNX</th><th>变化（百分点）</th></tr></thead><tbody><tr v-for="key in metricKeys" :key="key"><td>{{ key }}</td><td>{{ metric(report.reference_metrics[key]) }}</td><td>{{ metric(report.onnx_metrics[key]) }}</td><td>{{ metric(report.metric_deltas[key]) }}</td></tr></tbody></table></div>
        <div class="table-scroll"><table class="data-table"><thead><tr><th>CPU 推理测量</th><th>平均 ms/图</th><th>P50</th><th>P95</th><th>图/秒</th></tr></thead><tbody><tr v-for="[key, value] in Object.entries(report.benchmark)" :key="key"><td>{{ key }}</td><td>{{ value.mean_ms_per_image.toFixed(2) }}</td><td>{{ value.p50_ms_per_image.toFixed(2) }}</td><td>{{ value.p95_ms_per_image.toFixed(2) }}</td><td>{{ value.images_per_second.toFixed(2) }}</td></tr></tbody></table></div>
        <p class="muted-copy">单进程重复样本测量，包含预处理和后处理；不代表服务并发性能。</p>
        <dl class="definition-list"><dt>源权重 SHA-256</dt><dd class="mono">{{ report.source_sha256 }}</dd><dt>ONNX SHA-256</dt><dd class="mono">{{ report.artifact_sha256 }}</dd><dt>数据 SHA-256</dt><dd class="mono">{{ report.dataset_sha256 }}</dd></dl>
      </template>
      <form v-if="current.status === 'completed' && report?.quality_gate.passed" class="stacked-form" @submit.prevent="publishModel">
        <h3>发布为 ONNX 推理版本</h3>
        <p class="muted-copy">复制全部校验通过的模型与外部张量；发布后删除本次导出不会影响部署。当前部署支持 batch=1。</p>
        <div class="form-grid">
          <label>模型 ID<input v-model="publishKey" required pattern="[a-z0-9][a-z0-9-]{2,63}" placeholder="helmet-onnx" /></label>
          <label>模型名称<input v-model="publishName" maxlength="128" /></label>
          <label>版本号<input v-model="publishVersion" placeholder="留空自动递增" maxlength="64" /></label>
        </div>
        <button class="primary-button" :disabled="publishing || !publishKey.trim()">{{ publishing ? '正在发布…' : '发布并配置部署' }}</button>
      </form>
      <details><summary>配置与原始报告</summary><pre class="annotation-example">{{ JSON.stringify({ configuration: current.progress?.configuration, parameters: current.parameters, verification: current.progress?.export_verification, report }, null, 2) }}</pre></details>
      <details open><summary>任务日志</summary><pre class="export-logs">{{ logs.join('\n') || '等待日志' }}</pre></details>
      <div v-if="Object.keys(current.artifacts).length" class="stacked-form"><a :href="exportsApi.archiveUrl(current.id)">下载全部产物 ZIP</a><a v-for="[file, size] in Object.entries(current.artifacts)" :key="file" :href="exportsApi.artifactUrl(current.id, file)">{{ file }} · {{ formatBytes(size) }}</a></div>
    </section>
    <section class="section-block"><header class="section-heading"><h2>导出记录</h2></header><p v-if="!jobs.length" class="muted-copy">尚无导出任务。</p><div v-else class="table-scroll"><table class="data-table"><thead><tr><th>名称</th><th>状态</th><th>来源</th><th>创建时间</th></tr></thead><tbody><tr v-for="job in jobs" :key="job.id"><td><RouterLink :to="`/exports/${job.id}`">{{ job.name }}</RouterLink></td><td>{{ status(job) }}</td><td>{{ job.progress?.source?.label ?? job.model }}</td><td>{{ formatDateTime(job.created_at) }}</td></tr></tbody></table></div></section>
  </div>
</template>

<style scoped>
.export-logs { max-height: 28rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.mono { overflow-wrap: anywhere; }
</style>
