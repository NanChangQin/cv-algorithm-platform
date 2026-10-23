<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { systemApi, type GpuInfo } from '@/api/system'
import {
  activeTrainingStatuses, defaultParameters, defaultSplitConfig, formatMetric, progressRatio, progressText, trainingApi,
  trainingStatusLabel, trainingStatusTone,
  type TrainingDataset, type TrainingModel, type TrainingParameters, type TrainingRunSummary,
  type TrainingSourceOption, type TrainingRun,
} from '@/api/training'
import DatasetSplitForm from '@/components/training/DatasetSplitForm.vue'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import type { ParameterContract } from '@/types/algorithm'
import { parameterIssue } from '@/utils/parameters'
import type { ParameterValues } from '@/utils/parameters'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime, nodeLabel, relativeTime } from '@/utils/presentation'

const router = useRouter()
const route = useRoute()
const runs = shallowRef<TrainingRunSummary[]>([])
const datasets = ref<TrainingDataset[]>([])
const models = ref<TrainingModel[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const loading = ref(false)
const error = ref('')

// 新建训练
const creating = ref(false)
const runName = ref('')
const datasetId = ref('')
const model = ref('yolo26n.pt')
const nodeId = ref('')
const gpuId = ref('')
const parameters = shallowRef<TrainingParameters>({ ...defaultParameters })
const operation = ref<'train' | 'val'>('train')
const validationParameters = shallowRef<ParameterValues>({ batch: 16, imgsz: 640, split: 'val', conf: 0.001, iou: 0.7, plots: true, workers: 4 })
const submitting = ref(false)
const contract = ref<ParameterContract | null>(null)
const contractError = ref('')
const editorIssue = ref('')
const sourceKind = ref<'builtin' | 'managed'>('builtin')
const sources = ref<TrainingSourceOption[]>([])
const sourceKey = ref('')
const sourceError = ref('')
const weightProgress = ref<number | null>(null)
const resumeParent = shallowRef<TrainingRun | null>(null)
const resumeKeys = ['batch', 'imgsz', 'workers', 'cache']
const optionKey = (item: TrainingSourceOption) => `${item.source.kind}:${item.source.reference}:${item.source.checkpoint ?? 'best'}`
const selectedSource = computed(() => sources.value.find((item) => optionKey(item) === sourceKey.value))
const parameterSpecs = computed(() => Object.fromEntries(Object.entries(contract.value?.parameters ?? {}).filter(([name, spec]) => spec.status === 'configurable' && (!resumeParent.value || resumeKeys.includes(name)))))
const editableParameters = computed(() => operation.value === 'val' ? validationParameters.value : resumeParent.value ? Object.fromEntries(Object.entries(parameters.value).filter(([key]) => resumeKeys.includes(key))) : parameters.value)
function updateParameters(value: Record<string, import('@/utils/parameters').ParameterValue>) {
  if (operation.value === 'val') { validationParameters.value = value; return }
  parameters.value = (resumeParent.value ? { ...resumeParent.value.parameters, ...value } : value) as TrainingParameters
}
async function loadSources() {
  try { sources.value = await trainingApi.sources(); sourceError.value = '' }
  catch (reason) { sourceError.value = errorMessage(reason, '模型来源读取失败') }
}
async function importWeight(files: File[]) {
  if (!files[0]) return
  weightProgress.value = 0
  try {
    const uploaded = await trainingApi.uploadWeight(files[0], (ratio) => { weightProgress.value = ratio })
    await loadSources()
    sourceKey.value = `upload:${uploaded.id}:best`
    toast.success('权重已保存，兼容性将在隔离训练容器中检查')
  } catch (reason) { toast.error(errorMessage(reason, '权重上传失败')) }
  finally { weightProgress.value = null }
}
async function removeWeight() {
  const reference = selectedSource.value?.source.reference
  if (!reference || selectedSource.value?.source.kind !== 'upload' || !window.confirm('删除此上传权重？已提交训练的独立副本会保留。')) return
  try { await trainingApi.deleteWeight(reference); sourceKey.value = ''; await loadSources() }
  catch (reason) { toast.error(errorMessage(reason, '删除权重失败')) }
}
const restrictedSpecs = computed(() => Object.entries(contract.value?.parameters ?? {}).filter(([, spec]) => spec.status !== 'configurable'))
const warnings = computed(() => operation.value === 'val' ? [] : [
  (parameters.value.optimizer ?? 'auto') === 'auto' ? 'optimizer=auto：框架自动决定学习率，手填 lr0 不会生效' : '',
  parameters.value.save === false ? 'save=false：关闭常规 checkpoint 保存；中途停止可能没有权重，框架仍可能在最后一轮保存' : '',
  parameters.value.val === false ? 'val=false：关闭逐轮验证，框架仍可能执行最终验证或提前停止检查' : '',
  parameters.value.dis !== undefined ? 'dis：蒸馏损失需要教师模型，当前入口尚未接入教师模型选择' : '',
].filter(Boolean))
async function loadContract() {
  contractError.value = ''
  contract.value = null
  try { contract.value = await trainingApi.parameterContract(operation.value) }
  catch (reason) { contractError.value = errorMessage(reason, '参数契约加载失败') }
}

// 上传数据集
const uploadingName = ref('')
const uploadParent = ref('')
const splitConfig = ref({ ...defaultSplitConfig })
const splitForm = ref<InstanceType<typeof DatasetSplitForm> | null>(null)
const uploadProgress = ref<number | null>(null)
const lastImported = ref<TrainingDataset | null>(null)

const datasetNames = computed(() => Object.fromEntries(datasets.value.map((item) => [item.id, item.name])))
const gpuNodes = computed(() => nodes.value.filter((node) => gpus.value.some((gpu) => gpu.node_id === node.id)))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === nodeId.value))
const selectedDataset = computed(() => datasets.value.find((item) => item.id === datasetId.value))
const formIssue = computed(() => {
  if (weightProgress.value !== null) return '正在上传权重'
  if (sourceKind.value === 'managed' && !resumeParent.value && !selectedSource.value) return sourceError.value || '请选择模型来源或上传权重'
  if (resumeParent.value && !resumeParent.value.progress?.checkpoint?.resumable) return resumeParent.value.progress?.checkpoint?.reason || '来源训练没有可恢复的 checkpoint'
  if (!datasetId.value) return '请选择训练数据集'
  if (operation.value === 'val' && selectedDataset.value) {
    const split = String(validationParameters.value.split ?? 'val') as 'train' | 'val' | 'test'
    if (!selectedDataset.value.stats.splits[split]?.images) return `数据集没有 ${split} 划分图片`
  }
  if (!nodeId.value) return gpuNodes.value.length ? '请选择训练服务器' : '没有检测到带 GPU 的服务器，请在“高级编排 → 运行节点”注册 GPU 节点'
  if (!contract.value) return contractError.value || '正在加载参数契约'
  if (operation.value === 'train' && parameters.value.imgsz % 32) return '图片尺寸必须是 32 的倍数'
  if (editorIssue.value) return editorIssue.value
  return parameterIssue(parameterSpecs.value, editableParameters.value)
})
const hasActive = computed(() => runs.value.some((run) => activeTrainingStatuses.includes(run.status)))

async function refresh() {
  loading.value = true
  try {
    ;[runs.value, datasets.value] = await Promise.all([trainingApi.runs(), trainingApi.datasets()])
    error.value = ''
  } catch (reason) {
    error.value = errorMessage(reason, '加载训练数据失败')
  } finally { loading.value = false }
}
async function loadResources() {
  try {
    const resources = await systemApi.executionResources()
    nodes.value = resources.nodes
    gpus.value = resources.gpus
    if (!nodeId.value || !gpuNodes.value.some((node) => node.id === nodeId.value)) nodeId.value = gpuNodes.value[0]?.id ?? ''
  } catch (reason) {
    toast.error(errorMessage(reason, '读取服务器资源失败'))
  }
}

let timer: number | undefined
function schedule() {
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => { if (hasActive.value) await refresh(); schedule() }, 5000)
}
onMounted(async () => {
  await refresh()
  schedule()
  try { models.value = await trainingApi.models() } catch { /* 模型列表加载失败时表单里给出提示 */ }
  if (route.query.dataset) startCreating(String(route.query.dataset))
  if (route.query.resume || route.query.finetune || route.query.model || route.query.validate) {
    startCreating(undefined, route.query.operation === 'val' || route.query.validate ? 'val' : 'train')
    await loadSources()
    sourceKind.value = 'managed'
    try {
      if (route.query.resume || route.query.finetune || route.query.validate) {
        const parent = await trainingApi.run(String(route.query.resume || route.query.finetune || route.query.validate))
        datasetId.value = parent.dataset_id
        if (route.query.resume) {
          resumeParent.value = parent
          parameters.value = { ...parent.parameters }
          runName.value = `${parent.name} · 续训`
        } else sourceKey.value = `run:${parent.id}:${route.query.checkpoint === 'last' ? 'last' : 'best'}`
      } else sourceKey.value = `algorithm:${String(route.query.model)}:best`
    } catch (reason) { sourceError.value = errorMessage(reason, '读取来源训练失败') }
  }
})
onBeforeUnmount(() => window.clearTimeout(timer))
watch(nodeId, () => { if (!nodeGpus.value.some((gpu) => String(gpu.index) === gpuId.value)) gpuId.value = '' })

function startCreating(preselected?: string, mode: 'train' | 'val' = 'train') {
  creating.value = true
  operation.value = mode
  validationParameters.value = { batch: 16, imgsz: 640, split: 'val', conf: 0.001, iou: 0.7, plots: true, workers: 4 }
  runName.value = ''
  parameters.value = { ...defaultParameters }
  datasetId.value = preselected && datasets.value.some((item) => item.id === preselected) ? preselected : datasets.value[0]?.id ?? ''
  editorIssue.value = ''
  resumeParent.value = null
  sourceKind.value = 'builtin'
  sourceKey.value = ''
  void loadSources()
  void loadContract()
  void loadResources()
}

async function importDataset(files: File[]) {
  const file = files[0]
  if (!file || splitForm.value?.issue) return
  uploadProgress.value = 0
  try {
    const dataset = await trainingApi.uploadDataset(file, uploadingName.value.trim() || file.name.replace(/\.zip$/i, ''), (ratio) => { uploadProgress.value = ratio }, splitConfig.value, uploadParent.value || undefined)
    lastImported.value = dataset
    uploadingName.value = ''
    toast.success(`已导入“${dataset.name}”：${dataset.class_names.length} 个类别，${dataset.stats.splits.train.images + dataset.stats.splits.val.images + (dataset.stats.splits.test?.images ?? 0)} 张图片`)
    await refresh()
    if (creating.value) datasetId.value = dataset.id
  } catch (reason) {
    toast.error(errorMessage(reason, '数据集导入失败'))
  } finally { uploadProgress.value = null }
}

async function removeDataset(dataset: TrainingDataset) {
  if (!window.confirm(`删除数据集“${dataset.name}”？训练服务器上的副本也会删除。`)) return
  try {
    await trainingApi.deleteDataset(dataset.id)
    if (lastImported.value?.id === dataset.id) lastImported.value = null
    toast.info('已删除数据集')
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '删除失败'))
  }
}

async function submit() {
  if (formIssue.value || submitting.value) return
  submitting.value = true
  try {
    const run = await trainingApi.create({
      name: runName.value.trim(),
      dataset_id: datasetId.value,
      model: model.value,
      node_id: nodeId.value,
      gpu_device_ids: gpuId.value ? [gpuId.value] : [],
      parameters: operation.value === 'val' ? validationParameters.value : parameters.value,
      ...(operation.value === 'val' ? { operation: 'val' as const } : {}),
      ...(resumeParent.value ? { source: { kind: 'run' as const, reference: resumeParent.value.id, checkpoint: 'resume' as const, mode: 'resume' as const } }
        : sourceKind.value === 'managed' && selectedSource.value ? { source: selectedSource.value.source } : {}),
    })
    toast.success('训练已提交')
    await router.push(`/training/${run.id}`)
  } catch (reason) {
    toast.error(errorMessage(reason, '提交训练失败'))
  } finally { submitting.value = false }
}

// 类别多（例如 COCO 的 80 类）时先显示标注最多的几个
const CHIP_LIMIT = 12
const expanded = ref(new Set<string>())
function classCounts(dataset: TrainingDataset) {
  const entries = Object.entries(dataset.stats.instances).sort((a, b) => b[1] - a[1])
  return expanded.value.has(dataset.id) ? entries : entries.slice(0, CHIP_LIMIT)
}
function toggleClasses(id: string) {
  const next = new Set(expanded.value)
  if (!next.delete(id)) next.add(id)
  expanded.value = next
}
function totalImages(dataset: TrainingDataset) {
  return dataset.stats.splits.train.images + dataset.stats.splits.val.images + (dataset.stats.splits.test?.images ?? 0)
}
function percent(run: TrainingRunSummary) {
  const ratio = progressRatio(run)
  return ratio === null ? null : Math.round(ratio * 100)
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div>
      <h1>训练中心</h1>
      <p>上传标注数据和自定义权重，训练、微调或恢复目标检测模型；独立验证测试集，查看指标与图表，再发布为推理模型。</p>
    </div>
    <div v-if="!creating" class="toolbar-actions">
      <button type="button" class="secondary-button compact-button" @click="startCreating(undefined, 'val')">独立验证</button>
      <button type="button" class="primary-button compact-button" @click="startCreating()">新建训练</button>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section v-if="creating" class="section-block training-form" aria-labelledby="new-training-title">
    <header class="section-heading"><div><h2 id="new-training-title">{{ operation === 'val' ? '独立验证' : resumeParent ? '断点续训' : '新建训练' }}</h2></div><button type="button" class="text-button" @click="creating = false">取消</button></header>
    <p v-if="operation === 'val'" class="build-notice">单独执行模型评估，可选择 train / val / test 划分。结果包含完整 mAP、分类别指标、速度和混淆矩阵，不会更新模型权重。</p>
    <p v-if="resumeParent" class="build-notice">从“{{ resumeParent.name }}”第 {{ resumeParent.progress?.checkpoint?.epoch }} 轮之后恢复，继续原 {{ resumeParent.progress?.checkpoint?.epochs }} 轮计划。数据版本、optimizer 和学习率计划固定；可调整 batch、图片尺寸、workers 和 cache。需要其他改动请使用微调。</p>

    <h3>1. 数据集</h3>
    <div v-if="datasets.length" class="training-form-grid">
      <label class="field-label">训练数据集
        <select v-model="datasetId" :disabled="Boolean(resumeParent)">
          <option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · v{{ dataset.version ?? 1 }}（{{ dataset.class_names.length }} 类 · {{ totalImages(dataset) }} 张）</option>
        </select>
      </label>
      <p v-if="selectedDataset" class="muted-copy training-dataset-hint">
        训练集 {{ selectedDataset.stats.splits.train.images }} 张 · 验证集 {{ selectedDataset.stats.splits.val.images }} 张 · 测试集 {{ selectedDataset.stats.splits.test?.images ?? 0 }} 张 · 类别：{{ selectedDataset.class_names.slice(0, 8).join('、') }}<template v-if="selectedDataset.class_names.length > 8"> 等</template>
      </p>
    </div>
    <p v-else class="muted-copy">还没有数据集，先在下方上传一个 YOLO 格式的 ZIP。</p>

    <h3>2. 模型来源</h3>
    <div v-if="!resumeParent" class="toolbar-actions" role="radiogroup" aria-label="模型来源">
      <label><input v-model="sourceKind" type="radio" value="builtin" name="source-kind" /> 内置预训练模型</label>
      <label><input v-model="sourceKind" type="radio" value="managed" name="source-kind" /> 自定义权重 / 已有模型{{ operation === 'train' ? '微调' : '验证' }}</label>
    </div>
    <div v-if="!resumeParent && sourceKind === 'builtin'" class="model-options" role="radiogroup" aria-label="预训练模型">
      <label v-for="item in models" :key="item.key" :class="['model-option', { selected: model === item.key }]">
        <input v-model="model" type="radio" name="training-model" :value="item.key" />
        <strong>{{ item.label }}</strong><small>{{ item.description }}</small>
      </label>
      <p v-if="!models.length" class="muted-copy">模型列表加载失败，请刷新页面。</p>
    </div>

    <div v-if="!resumeParent && sourceKind === 'managed'" class="dataset-upload">
      <label class="field-label">初始化权重
        <select v-model="sourceKey" aria-label="初始化权重">
          <option value="">请选择上传权重、已发布模型或历史训练权重</option>
          <option v-for="item in sources" :key="optionKey(item)" :value="optionKey(item)">{{ item.source.kind === 'upload' ? '上传' : item.source.kind === 'algorithm' ? '已发布模型' : '历史训练' }} · {{ item.label }}</option>
        </select>
      </label>
      <p class="muted-copy">{{ operation === 'val' ? '选择要评估的模型权重，可独立指定数据版本与划分。' : '微调会创建新实验并重新初始化 optimizer，可选择新的数据集和完整训练参数。' }}上传 .pt 最多 2 GiB，仅支持 Ultralytics 目标检测权重；上传成功后，执行时检查兼容性。</p>
      <p v-if="selectedSource?.sha256" class="muted-copy mono" style="overflow-wrap: anywhere">SHA256 {{ selectedSource.sha256 }}</p>
      <button v-if="selectedSource?.source.kind === 'upload'" type="button" class="text-button" @click="removeWeight">删除上传权重</button>
      <UploadDropzone compact accept=".pt" :multiple="false" :progress="weightProgress" :disabled="weightProgress !== null" title="上传自定义 .pt 权重" hint="单个 Ultralytics 目标检测 .pt 权重，最多 2 GiB" @files="importWeight" />
      <p v-if="sourceError" class="alert" role="alert">{{ sourceError }}</p>
    </div>

    <h3>3. 训练服务器</h3>
    <div class="training-form-grid">
      <label class="field-label">服务器
        <select v-model="nodeId">
          <option v-if="!gpuNodes.length" value="">没有带 GPU 的服务器</option>
          <option v-for="node in gpuNodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option>
        </select>
      </label>
      <label class="field-label">GPU
        <select v-model="gpuId" :disabled="!nodeGpus.length">
          <option value="">自动（空闲显存最多）</option>
          <option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">
            GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB · 占用 {{ gpu.utilization_percent }}%
          </option>
        </select>
      </label>
    </div>

    <h3>4. {{ operation === 'val' ? '验证' : '训练' }}参数</h3>
    <template v-if="contract">
      <p class="muted-copy">Ultralytics {{ contract.framework_version }} · {{ Object.keys(parameterSpecs).length }} 个可配置参数</p>
      <ParameterForm :key="`${operation}:${resumeParent?.id ?? 'new'}`" :model-value="editableParameters" :parameters="parameterSpecs" @update:model-value="updateParameters" @validity="editorIssue = $event" />
      <p v-for="warning in warnings" :key="warning" class="build-notice">{{ warning }}</p>
      <details class="inline-details">
        <summary>平台管理与待接入参数（{{ restrictedSpecs.length }}）</summary>
        <dl><template v-for="[name, spec] in restrictedSpecs" :key="name"><dt><code>{{ name }}</code> · {{ spec.title }}</dt><dd>{{ spec.reason }}</dd></template></dl>
        <p class="muted-copy mono">契约 {{ contract.id }} · {{ contract.sha256 }}</p>
      </details>
    </template>
    <p v-else class="muted-copy">{{ contractError || '正在加载参数契约…' }} <button v-if="contractError" type="button" class="text-button" @click="loadContract">重试</button></p>
    <label class="field-label">名称（可选）<input v-model="runName" maxlength="120" :placeholder="selectedDataset ? `${selectedDataset.name} · ${model.replace('.pt', '')}` : '默认用“数据集 · 模型”命名'" /></label>
    <p v-if="formIssue" class="muted-copy">{{ formIssue }}</p>
    <button type="button" class="primary-button" :disabled="Boolean(formIssue) || submitting" @click="submit">{{ submitting ? '正在提交…' : operation === 'val' ? '开始验证' : '开始训练' }}</button>
  </section>

  <section class="section-block">
    <header class="section-heading"><div><h2>训练与验证记录</h2><span class="count-badge">{{ runs.length }}</span></div></header>
    <div v-if="loading && !runs.length" class="empty-state">正在加载…</div>
    <div v-else-if="!runs.length" class="empty-state"><strong>还没有训练</strong><p>先上传数据集，再点右上角“新建训练”。</p></div>
    <ul v-else class="training-list">
      <li v-for="run in runs" :key="run.id">
        <RouterLink :to="`/training/${run.id}`" class="training-row">
          <span class="training-title">
            <strong>{{ run.name }}</strong>
            <small>{{ run.progress?.operation === 'val' ? '独立验证 · ' : '' }}{{ run.model.replace('.pt', '') }} · {{ datasetNames[run.dataset_id] ?? '已删除的数据集' }} · {{ nodeLabel(run.node_id, nodes) }} GPU {{ run.gpu_device_ids.join(', ') }}</small>
          </span>
          <span :class="`task-status task-${trainingStatusTone[run.status]}`"><i class="status-dot" />{{ trainingStatusLabel[run.status] }}</span>
          <span class="training-progress">
            <template v-if="run.status === 'failed'"><small class="training-error">{{ run.error }}</small></template>
            <template v-else>
              <progress v-if="percent(run) !== null" :value="percent(run) ?? 0" max="100" :aria-label="`${run.name} 训练进度`" />
              <small>{{ progressText(run) || (run.finished_at ? `结束于 ${formatDateTime(run.finished_at)}` : '') }}</small>
            </template>
          </span>
          <span class="training-best">
            <small>mAP50-95</small><strong>{{ formatMetric(run.best?.map50_95) }}</strong>
          </span>
          <small class="training-time">{{ relativeTime(run.created_at) }}</small>
        </RouterLink>
      </li>
    </ul>
  </section>

  <section class="section-block">
    <header class="section-heading"><div><h2>数据集</h2><span class="count-badge">{{ datasets.length }}</span></div></header>
    <div class="dataset-upload">
      <label class="field-label">数据集名称（可选）<input v-model="uploadingName" maxlength="120" placeholder="默认使用 ZIP 文件名" /></label>
      <label class="field-label">版本归属
        <select v-model="uploadParent" :disabled="uploadProgress !== null">
          <option value="">创建新的数据集 · v1</option>
          <option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · 基于 v{{ dataset.version ?? 1 }} 上传新版本</option>
        </select>
      </label>
      <DatasetSplitForm ref="splitForm" v-model="splitConfig" :disabled="uploadProgress !== null" />
      <UploadDropzone compact accept=".zip,application/zip,application/x-zip-compressed" :multiple="false" :progress="uploadProgress" :disabled="uploadProgress !== null || Boolean(splitForm?.issue)" title="拖入 YOLO 格式的数据集 ZIP，或点击选择" hint="images/ 与 labels/ 目录 + data.yaml（或 classes.txt）；保留 train / val / test；原包、规范化包和校验报告均可追溯" @files="importDataset" />
      <details class="inline-details">
        <summary>支持哪些目录结构？</summary>
        <pre class="annotation-example">data.yaml              # names: [person, car]
images/train/xxx.jpg   labels/train/xxx.txt
images/val/yyy.jpg     labels/val/yyy.txt

# Roboflow 导出的 train/images、valid/images 同样可以
# 每行标注：类别编号 中心x 中心y 宽 高（0~1 归一化）
0 0.512 0.430 0.210 0.380</pre>
      </details>
    </div>
    <div v-if="lastImported && (lastImported.stats.warnings.length || lastImported.stats.invalid_examples.length)" class="build-notice" role="status">
      <strong>“{{ lastImported.name }}”导入时的提示</strong>
      <ul><li v-for="warning in lastImported.stats.warnings" :key="warning">{{ warning }}</li></ul>
      <details v-if="lastImported.stats.invalid_examples.length" class="inline-details">
        <summary>查看格式错误的标注</summary>
        <ul class="mono"><li v-for="line in lastImported.stats.invalid_examples" :key="line">{{ line }}</li></ul>
      </details>
    </div>
    <ul v-if="datasets.length" class="dataset-list">
      <li v-for="dataset in datasets" :key="dataset.id">
        <div>
          <strong>{{ dataset.name }} · v{{ dataset.version ?? 1 }}</strong>
          <small>
            训练 {{ dataset.stats.splits.train.images }} 张 · 验证 {{ dataset.stats.splits.val.images }} 张 · 测试 {{ dataset.stats.splits.test?.images ?? 0 }} 张{{ dataset.stats.auto_split ? '（自动划分）' : '' }}
            · {{ dataset.stats.splits.train.boxes + dataset.stats.splits.val.boxes + (dataset.stats.splits.test?.boxes ?? 0) }} 个标注框 · {{ formatBytes(dataset.size_bytes) }} · {{ formatDateTime(dataset.created_at) }}
          </small>
          <span class="class-chips">
            <span v-for="[label, count] in classCounts(dataset)" :key="label">{{ label }} <b>{{ count }}</b></span>
            <button v-if="dataset.class_names.length > CHIP_LIMIT" type="button" class="text-button" @click="toggleClasses(dataset.id)">
              {{ expanded.has(dataset.id) ? '收起' : `全部 ${dataset.class_names.length} 类` }}
            </button>
          </span>
        </div>
        <span class="toolbar-actions">
          <RouterLink :to="`/training/datasets/${dataset.id}`" class="secondary-button compact-button">详情与预览</RouterLink>
          <button type="button" class="secondary-button compact-button" @click="startCreating(dataset.id)">用它训练</button>
          <button type="button" class="text-button" @click="removeDataset(dataset)">删除</button>
        </span>
      </li>
    </ul>
  </section>
</template>
