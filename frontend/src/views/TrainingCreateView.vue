<script setup lang="ts">
import { computed, nextTick, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { systemApi, type GpuInfo } from '@/api/system'
import {
  defaultParameters, trainingApi,
  type TrainingDataset, type TrainingModel, type TrainingParameters,
  type TrainingSourceOption, type TrainingRun,
} from '@/api/training'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import type { ParameterContract } from '@/types/algorithm'
import { parameterIssue } from '@/utils/parameters'
import type { ParameterValues } from '@/utils/parameters'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { nodeLabel } from '@/utils/presentation'
import TrainingTaskTypes from '@/components/training/TrainingTaskTypes.vue'
import { trainingModelTask, trainingTaskLabel } from '@/utils/trainingTasks'

const router = useRouter()
const route = useRoute()
const datasets = ref<TrainingDataset[]>([])
const models = ref<TrainingModel[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const resourcesLoading = ref(false)
const resourcesError = ref('')
const initializing = ref(true)
const step = ref(1)
const steps = ['任务、算法与权重', '数据与设备', '参数与提交']
const error = ref('')
const copyNotice = ref('')

// 新建训练
const runName = ref('')
const datasetId = ref('')
const model = ref('yolo11s.pt')
const taskType = ref('object_detection')
const algorithm = ref('yolo11')
const modelAlgorithm = (item: TrainingModel) => item.algorithm ?? item.key.match(/^yolo\d+/)?.[0] ?? ''
const algorithms = computed(() => [...new Set(models.value.filter(item => trainingModelTask(item) === taskType.value).map(modelAlgorithm))])
const availableModels = computed(() => models.value.filter((item) => trainingModelTask(item) === taskType.value && modelAlgorithm(item) === algorithm.value))
const isFaster = computed(() => algorithm.value === 'fasterrcnn')
const algorithmLabel = (key: string) => key === 'fasterrcnn' ? 'Faster R-CNN' : key.toUpperCase()
async function chooseAlgorithm() {
  model.value = availableModels.value.find((item) => item.scale === 's')?.key ?? availableModels.value[0]?.key ?? ''
  sourceKind.value = 'builtin'; sourceKey.value = ''; editorIssue.value = ''
  await loadContract(true)
  await loadResources()
}
function selectArchitecture(value?: string | null) {
  const item = models.value.find((item) => item.key === value)
  if (item) { taskType.value = trainingModelTask(item); algorithm.value = modelAlgorithm(item); model.value = item.key }
}
const trainingMode = computed({
  get: () => sourceKind.value === 'managed' ? 'managed' : parameters.value.pretrained === false ? 'scratch' : 'pretrained',
  set: (value: string) => {
    sourceKind.value = value === 'managed' ? 'managed' : 'builtin'
    parameters.value = { ...parameters.value, pretrained: value !== 'scratch', ...(isFaster.value ? {trainable_backbone_layers:value === 'scratch' ? 5 : 3} : {}) }
  },
})
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
const resumeKeys = computed(() => isFaster.value ? ['batch', 'workers', 'device', 'plots'] : ['batch', 'imgsz', 'workers', 'cache'])
const optionKey = (item: TrainingSourceOption) => `${item.source.kind}:${item.source.reference}:${item.source.checkpoint ?? 'best'}`
const selectedSource = computed(() => sources.value.find((item) => optionKey(item) === sourceKey.value))
const parameterSpecs = computed(() => Object.fromEntries(Object.entries(contract.value?.parameters ?? {}).filter(([name, spec]) => spec.status === 'configurable' && (!resumeParent.value || resumeKeys.value.includes(name)))))
const editableParameters = computed(() => operation.value === 'val' ? validationParameters.value : resumeParent.value ? Object.fromEntries(Object.entries(parameters.value).filter(([key]) => resumeKeys.value.includes(key))) : parameters.value)
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
const warnings = computed(() => operation.value === 'val' || isFaster.value ? [] : [
  (parameters.value.optimizer ?? 'auto') === 'auto' ? 'optimizer=auto：框架自动决定学习率，手填 lr0 不会生效' : '',
  parameters.value.save === false ? 'save=false：关闭常规 checkpoint 保存；中途停止可能没有权重，框架仍可能在最后一轮保存' : '',
  parameters.value.val === false ? 'val=false：关闭逐轮验证，框架仍可能执行最终验证或提前停止检查' : '',
  parameters.value.dis !== undefined ? 'dis：蒸馏损失需要教师模型，当前入口尚未接入教师模型选择' : '',
].filter(Boolean))
let contractSequence = 0
async function loadContract(reset = false) {
  const sequence = ++contractSequence
  contractError.value = ''
  contract.value = null
  try {
    const value = isFaster.value ? await trainingApi.parameterContract(operation.value, model.value) : await trainingApi.parameterContract(operation.value)
    if (sequence !== contractSequence) return
    contract.value = value
    if (reset) {
      const defaults = Object.fromEntries(Object.entries(value.parameters).filter(([,s]) => s.status === 'configurable').map(([k,s]) => [k,s.default])) as ParameterValues
      if (operation.value === 'val') validationParameters.value = defaults
      else parameters.value = defaults as TrainingParameters
    }
  }
  catch (reason) { contractError.value = errorMessage(reason, '参数契约加载失败') }
}

const deviceChoice = computed({get: () => String((operation.value === 'val' ? validationParameters.value : parameters.value).device ?? 'auto'),set: (value: string) => { if (operation.value === 'val') validationParameters.value = {...validationParameters.value,device:value}; else parameters.value = {...parameters.value,device:value}; gpuId.value=''; if (value === 'cpu' && !nodeId.value) nodeId.value=nodes.value[0]?.id??'' }})
const useCpu = computed(() => isFaster.value && deviceChoice.value === 'cpu')
const gpuNodes = computed(() => useCpu.value ? nodes.value : nodes.value.filter((node) => gpus.value.some((gpu) => gpu.node_id === node.id)))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === nodeId.value))
const selectedDataset = computed(() => datasets.value.find((item) => item.id === datasetId.value))
const modelIssue = computed(() => {
  if (taskType.value !== 'object_detection') return '该任务类型尚未接入'
  if (!resumeParent.value && !availableModels.value.some((item) => item.key === model.value)) return '请选择可用的算法和模型规格'
  if (weightProgress.value !== null) return '正在上传权重'
  if (!resumeParent.value && sourceKind.value === 'managed' && selectedSource.value?.architecture && models.value.some(m => m.key === selectedSource.value?.architecture) && selectedSource.value.architecture !== model.value) return '所选算法规格与来源权重不一致，请重新选择'
  if (!resumeParent.value && sourceKind.value === 'managed' && parameters.value.pretrained === false) return '已有权重微调需要启用 pretrained；从头训练请选择内置模型结构'
  if (sourceKind.value === 'managed' && !resumeParent.value && !selectedSource.value) return sourceError.value || '请选择模型来源或上传权重'
  if (resumeParent.value && !resumeParent.value.progress?.checkpoint?.resumable) return resumeParent.value.progress?.checkpoint?.reason || '来源训练没有可恢复的 checkpoint'
  return ''
})
const dataIssue = computed(() => {
  if (!datasetId.value || !selectedDataset.value) return '请选择可用的训练数据集版本'
  if (operation.value === 'val' && selectedDataset.value) {
    const split = String(validationParameters.value.split ?? 'val') as 'train' | 'val' | 'test'
    if (!selectedDataset.value.stats.splits[split]?.images) return `数据集没有 ${split} 划分图片`
  }
  if (resourcesLoading.value) return '正在读取训练设备…'
  if (resourcesError.value) return resourcesError.value
  if (!nodeId.value) return gpuNodes.value.length ? '请选择训练服务器' : '没有检测到带 GPU 的服务器，请在“高级编排 → 运行节点”注册 GPU 节点'
  return ''
})
const formIssue = computed(() => {
  if (modelIssue.value || dataIssue.value) return modelIssue.value || dataIssue.value
  if (!contract.value) return contractError.value || '正在加载参数契约'
  if (!isFaster.value && operation.value === 'train' && parameters.value.imgsz % 32) return '图片尺寸必须是 32 的倍数'
  if (editorIssue.value) return editorIssue.value
  return parameterIssue(parameterSpecs.value, editableParameters.value)
})
async function refreshDatasets() {
  try {
    datasets.value = await trainingApi.datasets()
    if (!datasets.value.some((item) => item.id === datasetId.value)) datasetId.value = datasets.value[0]?.id ?? ''
  } catch (reason) { toast.error(errorMessage(reason, '数据集读取失败')) }
}
async function loadResources() {
  resourcesLoading.value = true
  resourcesError.value = ''
  try {
    const resources = await systemApi.executionResources()
    nodes.value = resources.nodes
    gpus.value = resources.gpus
    if (!nodeId.value || !gpuNodes.value.some((node) => node.id === nodeId.value)) nodeId.value = gpuNodes.value[0]?.id ?? ''
  } catch (reason) {
    resourcesError.value = errorMessage(reason, '读取服务器资源失败')
  } finally { resourcesLoading.value = false }
}

async function initialize() {
  initializing.value = true
  error.value = ''
  copyNotice.value = ''
  step.value = 1
  try {
    ;[datasets.value, models.value] = await Promise.all([trainingApi.datasets(), trainingApi.models()])
    startCreating(route.query.dataset ? String(route.query.dataset) : undefined,
      route.query.operation === 'val' || route.query.validate ? 'val' : 'train')
    if (route.query.task_type && route.query.task_type !== 'object_detection') throw new Error('该任务类型尚未接入，请选择目标检测')
    await loadSources()
    if (typeof route.query.copy === 'string') {
      const original = await trainingApi.run(route.query.copy)
      operation.value = original.progress?.operation === 'val' ? 'val' : 'train'
      runName.value = `${original.name} · 副本`
      datasetId.value = original.dataset_id
      const architecture = original.progress?.architecture ?? original.model
      if (!models.value.some(m => m.key === architecture)) throw new Error('该历史任务未记录可识别的模型结构，无法可靠复用；请新建训练并手动选择结构与权重')
      selectArchitecture(architecture)
      await loadContract(true)
      if (operation.value === 'val') validationParameters.value = { ...original.parameters }
      else parameters.value = { ...original.parameters }
      const source = original.progress?.source
      if (source && source.kind !== 'builtin') {
        sourceKind.value = 'managed'
        sourceKey.value = `${source.kind}:${source.reference}:${source.mode === 'resume' ? 'best' : source.checkpoint ?? 'best'}`
      }
      await loadResources()
      if (gpuNodes.value.some(n => n.id === original.node_id)) nodeId.value = original.node_id
      await nextTick()
      if (nodeId.value === original.node_id && original.gpu_device_ids.length === 1 && nodeGpus.value.some(g => String(g.index) === original.gpu_device_ids[0])) gpuId.value = original.gpu_device_ids[0]!
      copyNotice.value = source?.mode === 'resume' ? '已复制配置，使用原来源 best 权重开始新的微调；不会恢复 optimizer 或继续原任务。请确认权重仍可用。' : '已复制算法、初始化来源、数据版本和参数。将创建独立实验，不复制训练结果，也不自动开始执行。'
    }
    if (route.query.resume || route.query.finetune || route.query.model || route.query.validate) {
      sourceKind.value = 'managed'
      if (route.query.resume || route.query.finetune || route.query.validate) {
        const parent = await trainingApi.run(String(route.query.resume || route.query.finetune || route.query.validate))
        datasetId.value = parent.dataset_id
        selectArchitecture(parent.progress?.architecture ?? parent.model)
        await loadContract(true)
        if (route.query.resume) {
          resumeParent.value = parent
          parameters.value = { ...parent.parameters }
          runName.value = `${parent.name} · 续训`
        } else sourceKey.value = `run:${parent.id}:${route.query.checkpoint === 'last' ? 'last' : 'best'}`
      } else {
        sourceKey.value = `algorithm:${String(route.query.model)}:best`
        selectArchitecture(selectedSource.value?.architecture)
        await loadContract(true)
      }
    }
  } catch (reason) { error.value = errorMessage(reason, '训练配置加载失败，请重试') }
  finally { initializing.value = false }
}
onMounted(initialize)
watch(() => route.fullPath, initialize)
watch(useCpu, () => { if (useCpu.value && !nodeId.value) nodeId.value=nodes.value[0]?.id??'' })
watch(nodeId, () => { if (!nodeGpus.value.some((gpu) => String(gpu.index) === gpuId.value)) gpuId.value = '' })

function startCreating(preselected?: string, mode: 'train' | 'val' = 'train') {
  operation.value = mode
  validationParameters.value = { batch: 16, imgsz: 640, split: 'val', conf: 0.001, iou: 0.7, plots: true, workers: 4 }
  runName.value = ''
  parameters.value = { ...defaultParameters, pretrained: true, plots: true }
  taskType.value = 'object_detection'
  algorithm.value = 'yolo11'
  model.value = 'yolo11s.pt'
  datasetId.value = preselected && datasets.value.some((item) => item.id === preselected) ? preselected : datasets.value[0]?.id ?? ''
  editorIssue.value = ''
  resumeParent.value = null
  sourceKind.value = 'builtin'
  sourceKey.value = ''
  void loadContract()
  void loadResources()
}

async function submit() {
  if (step.value !== 3 || error.value || initializing.value || formIssue.value || submitting.value) return
  submitting.value = true
  try {
    const run = await trainingApi.create({
      task_type: 'object_detection',
      name: runName.value.trim(),
      dataset_id: datasetId.value,
      model: model.value,
      ...(!resumeParent.value ? { architecture: model.value } : {}),
      node_id: nodeId.value,
      gpu_device_ids: !useCpu.value && gpuId.value ? [gpuId.value] : [],
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

function totalImages(dataset: TrainingDataset) {
  return Object.values(dataset.stats.splits).reduce((sum, split) => sum + (split?.images ?? 0), 0)
}
const pageTitle = computed(() => operation.value === 'val' ? '独立验证' : resumeParent.value ? '断点续训' : '新建训练')
const stepIssue = computed(() => {
  if (step.value === 1) return modelIssue.value
  if (step.value === 2) return modelIssue.value || dataIssue.value
  return formIssue.value
})
function goStep(next: number) {
  if (next > step.value && stepIssue.value) return
  step.value = next
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <RouterLink class="back-link" to="/training">← 训练中心</RouterLink>
  <header class="page-heading page-heading-actions">
    <div><h1>{{ pageTitle }}</h1><p>先选任务类型、算法和规格，再配置权重、数据、设备与参数。</p></div>
    <RouterLink to="/training" class="secondary-button compact-button">取消</RouterLink>
  </header>
  <p v-if="initializing" class="empty-state">正在加载训练配置…</p>
  <div v-else-if="error" class="alert" role="alert">{{ error }} <button type="button" class="text-button" @click="initialize">重试</button></div>
  <section v-else class="section-block training-form" aria-label="训练配置">
    <p v-if="copyNotice" class="build-notice">{{ copyNotice }}</p>
    <ol class="training-steps" aria-label="创建步骤"><li v-for="(label, index) in steps" :key="label" :class="{ current: step === index + 1, done: step > index + 1 }" :aria-current="step === index + 1 ? 'step' : undefined"><span>{{ index + 1 }}</span>{{ label }}</li></ol>
    <p v-if="step === 1 && operation === 'val'" class="build-notice">单独执行模型评估，可选择 train / val / test 划分。结果包含完整 mAP、分类别指标、速度和混淆矩阵，不会更新模型权重。</p>
    <p v-if="step === 1 && resumeParent" class="build-notice">从“{{ resumeParent.name }}”第 {{ resumeParent.progress?.checkpoint?.epoch }} 轮之后恢复，继续原 {{ resumeParent.progress?.checkpoint?.epochs }} 轮计划。数据版本、optimizer 和学习率计划固定；可调整 batch、图片尺寸、workers 和 cache。需要其他改动请使用微调。</p>

    <div v-show="step === 1" class="training-step">
      <h3>任务类型</h3>
      <TrainingTaskTypes v-model="taskType" :locked="Boolean(resumeParent)" />
      <h3>算法与模型规格</h3>
      <p v-if="resumeParent" class="build-notice">沿用原训练算法与结构：{{ resumeParent.progress?.architecture ?? resumeParent.model }}，续训不更换模型规格。</p>
      <div v-else class="training-form-grid algorithm-selection">
        <label class="field-label">算法
          <select v-model="algorithm" aria-label="训练算法" :disabled="Boolean(resumeParent)" @change="chooseAlgorithm">
            <option v-for="item in algorithms" :key="item" :value="item">{{ algorithmLabel(item) }}{{ item === 'yolo11' ? ' · 默认' : '' }}</option>
          </select>
        </label>
        <p class="muted-copy">{{ trainingTaskLabel(taskType) }}：先确定算法和规格，再选择初始化方式、数据和设备。</p>
      </div>
      <div v-if="!resumeParent" class="model-options" role="radiogroup" aria-label="模型规格">
        <label v-for="item in availableModels" :key="item.key" :class="['model-option', { selected: model === item.key }]">
          <input v-model="model" type="radio" name="training-model" :value="item.key" :disabled="Boolean(resumeParent)" />
          <strong>{{ item.label }}</strong><small>{{ item.description }}</small>
        </label>
      </div>
      <p v-if="!models.length" class="alert" role="alert">算法列表加载失败，请刷新页面。</p>
      <p class="muted-copy">全部规格均可选择。显存需求还取决于图片尺寸和 batch；大规格可先用较小 batch 验证。</p>

      <h3>{{ operation === 'val' ? '权重来源' : '训练方式与权重' }}</h3>
      <div v-if="!resumeParent" class="training-mode-options" role="radiogroup" aria-label="训练方式">
        <label><input v-model="trainingMode" type="radio" value="pretrained" name="training-mode" /> {{ operation === 'val' ? '官方预训练权重' : '官方预训练权重微调' }}</label>
        <label v-if="operation === 'train'"><input v-model="trainingMode" type="radio" value="scratch" name="training-mode" /> 从头训练</label>
        <label><input v-model="trainingMode" type="radio" value="managed" name="training-mode" /> 自定义权重 / 已有模型</label>
      </div>
      <p v-if="!resumeParent && sourceKind === 'builtin'" class="build-notice">{{ trainingMode === 'scratch' ? `使用 ${model.replace('.pt', '')} 结构随机初始化，不加载预训练参数。` : `使用官方 ${model} 初始化；算法与规格由上方选择决定。` }}</p>
      <div v-if="!resumeParent && sourceKind === 'managed'" class="dataset-upload">
        <label class="field-label">初始化权重
          <select v-model="sourceKey" aria-label="初始化权重">
            <option value="">请选择上传权重、已发布模型或历史训练权重</option>
            <option v-for="item in sources" :key="optionKey(item)" :value="optionKey(item)">{{ item.source.kind === 'upload' ? '上传' : item.source.kind === 'algorithm' ? '已发布模型' : '历史训练' }} · {{ item.label }}</option>
          </select>
        </label>
        <p class="muted-copy">{{ operation === 'val' ? '选择要评估的模型权重，可独立指定数据版本与划分。' : '微调会创建新实验并重新初始化 optimizer，可选择新的数据集和完整训练参数。' }}{{ isFaster ? '上传平台 Faster R-CNN .pt / .pth 权重，须包含模型结构和类别映射；验证要求类别顺序一致。' : '上传 Ultralytics 目标检测 .pt 权重；执行时检查兼容性。' }}最多 2 GiB。</p>
        <p v-if="selectedSource?.sha256" class="muted-copy mono" style="overflow-wrap: anywhere">SHA256 {{ selectedSource.sha256 }}</p>
        <button v-if="selectedSource?.source.kind === 'upload'" type="button" class="text-button" @click="removeWeight">删除上传权重</button>
        <UploadDropzone compact :accept="isFaster ? '.pt,.pth' : '.pt'" :multiple="false" :progress="weightProgress" :disabled="weightProgress !== null" :title="isFaster ? '上传 Faster R-CNN 权重' : '上传自定义 .pt 权重'" hint="单个权重文件，最多 2 GiB" @files="importWeight" />
        <p v-if="sourceError" class="alert" role="alert">{{ sourceError }}</p>
      </div>

    </div>
    <div v-show="step === 2" class="training-step">
      <h3>选择数据集版本</h3>
      <div v-if="datasets.length" class="training-form-grid">
        <label class="field-label">训练数据集
          <select v-model="datasetId" aria-label="训练数据集" :disabled="Boolean(resumeParent)">
            <option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }} · v{{ dataset.version ?? 1 }}（{{ dataset.class_names.length }} 类 · {{ totalImages(dataset) }} 张）</option>
          </select>
        </label>
        <p v-if="selectedDataset" class="muted-copy training-dataset-hint">
          训练集 {{ selectedDataset.stats.splits.train.images }} 张 · 验证集 {{ selectedDataset.stats.splits.val.images }} 张 · 测试集 {{ selectedDataset.stats.splits.test?.images ?? 0 }} 张 · 类别：{{ selectedDataset.class_names.slice(0, 8).join('、') }}<template v-if="selectedDataset.class_names.length > 8"> 等</template>
        </p>
      </div>
      <p v-else class="build-notice">还没有训练数据集。先在数据集管理中导入图片和标注，返回这里后刷新列表。</p>
      <div class="toolbar-actions dataset-picker-actions">
        <RouterLink to="/training/datasets" target="_blank" rel="noopener" class="text-button">管理数据集 ↗</RouterLink>
        <button type="button" class="text-button" @click="refreshDatasets">刷新数据集列表</button>
        <RouterLink v-if="selectedDataset" :to="`/training/datasets/${selectedDataset.id}`" target="_blank" rel="noopener" class="text-button">预览所选版本 ↗</RouterLink>
      </div>

      <h3>训练设备</h3>
      <label v-if="isFaster" class="field-label">执行设备<select v-model="deviceChoice" aria-label="执行设备"><option value="auto">GPU（自动选择或指定）</option><option value="cpu">CPU（适合小数据验证）</option></select></label>
      <p v-if="useCpu" class="build-notice">CPU 训练速度较慢，建议先使用小数据集与较小图片尺寸验证流程。</p>
      <p v-if="resourcesLoading" class="muted-copy" role="status">正在读取服务器和 GPU 资源…</p>
      <p v-if="resourcesError" class="alert" role="alert">{{ resourcesError }}</p>
      <button type="button" class="text-button" :disabled="resourcesLoading" @click="loadResources">刷新设备</button>
      <div class="training-form-grid">
        <label class="field-label">服务器
          <select v-model="nodeId" aria-label="训练服务器" :disabled="resourcesLoading">
            <option v-if="!gpuNodes.length" value="">{{ resourcesLoading ? '正在读取设备…' : '没有带 GPU 的服务器' }}</option>
            <option v-for="node in gpuNodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option>
          </select>
        </label>
        <label v-if="!useCpu" class="field-label">GPU
          <select v-model="gpuId" aria-label="训练 GPU" :disabled="!nodeGpus.length">
            <option value="">自动（空闲显存最多）</option>
            <option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">
              GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB · 占用 {{ gpu.utilization_percent }}%
            </option>
          </select>
        </label>
      </div>

    </div>
    <div v-show="step === 3" class="training-step">
      <h3>{{ operation === 'val' ? '验证' : '训练' }}参数</h3>
      <template v-if="contract">
        <p class="muted-copy">常用参数优先显示，其余参数可按名称或分组查找。</p>
        <ParameterForm :key="`${model}:${operation}:${resumeParent?.id ?? 'new'}`" :model-value="editableParameters" :parameters="parameterSpecs" @update:model-value="updateParameters" @validity="editorIssue = $event" />
        <p v-for="warning in warnings" :key="warning" class="build-notice">{{ warning }}</p>
        <details class="inline-details">
          <summary>平台管理与待接入参数（{{ restrictedSpecs.length }}）</summary>
          <dl><template v-for="[name, spec] in restrictedSpecs" :key="name"><dt><code>{{ name }}</code> · {{ spec.title }}</dt><dd>{{ spec.reason }}</dd></template></dl>
          <p class="muted-copy mono">{{ contract.framework }} {{ contract.framework_version }} · 契约 {{ contract.id }} · {{ contract.sha256 }}</p>
        </details>
      </template>
      <p v-else class="muted-copy">{{ contractError || '正在加载参数契约…' }} <button v-if="contractError" type="button" class="text-button" @click="loadContract()">重试</button></p>
      <h3>确认配置</h3>
      <div class="training-review" aria-label="训练配置摘要">
        <strong>{{ isFaster ? 'Faster R-CNN ResNet50-FPN' : resumeParent ? resumeParent.progress?.architecture ?? resumeParent.model : model.replace('.pt', '').toUpperCase() }} · {{ trainingTaskLabel(taskType) }}</strong>
        <span>{{ selectedDataset?.name ?? '未选择数据集' }} · v{{ selectedDataset?.version ?? 1 }}</span>
        <span>{{ nodeLabel(nodeId, nodes) }} · {{ useCpu ? 'CPU' : `GPU ${gpuId || '自动选择'}` }}</span>
        <span>{{ operation === 'val' ? '独立验证' : resumeParent ? '断点续训' : trainingMode === 'scratch' ? '从头训练' : '权重微调' }} · 图片 {{ editableParameters.imgsz ?? 640 }} · batch {{ editableParameters.batch ?? 16 }}</span>
      </div>
      <label class="field-label training-name">名称（可选）<input v-model="runName" maxlength="120" :placeholder="selectedDataset ? `${selectedDataset.name} · ${model.replace('.pt', '')}` : '默认用“数据集 · 模型”命名'" /></label>
    </div>
    <footer class="training-step-footer">
      <p v-if="stepIssue" class="muted-copy" role="status">{{ stepIssue }}</p>
      <div class="toolbar-actions">
        <button v-if="step > 1" type="button" class="secondary-button" :disabled="submitting" @click="goStep(step - 1)">上一步</button>
        <button v-if="step < 3" type="button" class="primary-button compact-button" :disabled="Boolean(stepIssue)" @click="goStep(step + 1)">下一步</button>
        <button v-else type="button" class="primary-button compact-button" :disabled="Boolean(formIssue) || submitting" @click="submit">{{ submitting ? '正在提交…' : operation === 'val' ? '开始验证' : '开始训练' }}</button>
      </div>
    </footer>
  </section>
</template>
