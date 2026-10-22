<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { systemApi, type GpuInfo } from '@/api/system'
import {
  activeTrainingStatuses, defaultParameters, formatMetric, progressRatio, progressText, trainingApi,
  trainingStatusLabel, trainingStatusTone,
  type TrainingDataset, type TrainingModel, type TrainingParameters, type TrainingRunSummary,
} from '@/api/training'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime, nodeLabel, relativeTime } from '@/utils/presentation'

const router = useRouter()
const route = useRoute()
const runs = ref<TrainingRunSummary[]>([])
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
const parameters = ref<TrainingParameters>({ ...defaultParameters })
const submitting = ref(false)

// 上传数据集
const uploadingName = ref('')
const uploadProgress = ref<number | null>(null)
const lastImported = ref<TrainingDataset | null>(null)

const datasetNames = computed(() => Object.fromEntries(datasets.value.map((item) => [item.id, item.name])))
const gpuNodes = computed(() => nodes.value.filter((node) => gpus.value.some((gpu) => gpu.node_id === node.id)))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === nodeId.value))
const selectedDataset = computed(() => datasets.value.find((item) => item.id === datasetId.value))
const formIssue = computed(() => {
  if (!datasetId.value) return '请选择训练数据集'
  if (!nodeId.value) return gpuNodes.value.length ? '请选择训练服务器' : '没有检测到带 GPU 的服务器，请在“高级编排 → 运行节点”注册 GPU 节点'
  if (parameters.value.imgsz % 32) return '图片尺寸必须是 32 的倍数'
  if (parameters.value.batch === 0) return 'batch 为 -1（自动）或正整数'
  return ''
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
})
onBeforeUnmount(() => window.clearTimeout(timer))
watch(nodeId, () => { if (!nodeGpus.value.some((gpu) => String(gpu.index) === gpuId.value)) gpuId.value = '' })

function startCreating(preselected?: string) {
  creating.value = true
  runName.value = ''
  parameters.value = { ...defaultParameters }
  datasetId.value = preselected && datasets.value.some((item) => item.id === preselected) ? preselected : datasets.value[0]?.id ?? ''
  void loadResources()
}

async function importDataset(files: File[]) {
  const file = files[0]
  if (!file) return
  uploadProgress.value = 0
  try {
    const dataset = await trainingApi.uploadDataset(file, uploadingName.value.trim() || file.name.replace(/\.zip$/i, ''), (ratio) => { uploadProgress.value = ratio })
    lastImported.value = dataset
    uploadingName.value = ''
    toast.success(`已导入“${dataset.name}”：${dataset.class_names.length} 个类别，${dataset.stats.splits.train.images + dataset.stats.splits.val.images} 张图片`)
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
      parameters: parameters.value,
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
  return dataset.stats.splits.train.images + dataset.stats.splits.val.images
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
      <p>上传 YOLO 格式的标注数据，选择服务器和 GPU 训练目标检测模型，训练过程中查看损失和精度曲线，结束后下载权重或发布为推理模型。</p>
    </div>
    <button v-if="!creating" type="button" class="primary-button compact-button" @click="startCreating()">新建训练</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section v-if="creating" class="section-block training-form" aria-labelledby="new-training-title">
    <header class="section-heading"><div><h2 id="new-training-title">新建训练</h2></div><button type="button" class="text-button" @click="creating = false">取消</button></header>

    <h3>1. 数据集</h3>
    <div v-if="datasets.length" class="training-form-grid">
      <label class="field-label">训练数据集
        <select v-model="datasetId">
          <option v-for="dataset in datasets" :key="dataset.id" :value="dataset.id">{{ dataset.name }}（{{ dataset.class_names.length }} 类 · {{ totalImages(dataset) }} 张）</option>
        </select>
      </label>
      <p v-if="selectedDataset" class="muted-copy training-dataset-hint">
        训练集 {{ selectedDataset.stats.splits.train.images }} 张 · 验证集 {{ selectedDataset.stats.splits.val.images }} 张 · 类别：{{ selectedDataset.class_names.slice(0, 8).join('、') }}<template v-if="selectedDataset.class_names.length > 8"> 等</template>
      </p>
    </div>
    <p v-else class="muted-copy">还没有数据集，先在下方上传一个 YOLO 格式的 ZIP。</p>

    <h3>2. 预训练模型</h3>
    <div class="model-options" role="radiogroup" aria-label="预训练模型">
      <label v-for="item in models" :key="item.key" :class="['model-option', { selected: model === item.key }]">
        <input v-model="model" type="radio" name="training-model" :value="item.key" />
        <strong>{{ item.label }}</strong><small>{{ item.description }}</small>
      </label>
      <p v-if="!models.length" class="muted-copy">模型列表加载失败，请刷新页面。</p>
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

    <h3>4. 训练参数</h3>
    <div class="training-parameter-grid">
      <label class="field-label">训练轮数 epochs<input v-model.number="parameters.epochs" type="number" min="1" max="1000" /></label>
      <label class="field-label">批大小 batch<input v-model.number="parameters.batch" type="number" min="-1" max="512" /><small>-1 表示按显存自动选择</small></label>
      <label class="field-label">图片尺寸 imgsz<input v-model.number="parameters.imgsz" type="number" min="32" max="2048" step="32" /></label>
      <label class="field-label">初始学习率 lr0<input v-model.number="parameters.lr0" type="number" min="0.00001" max="1" step="0.001" /></label>
    </div>
    <details class="inline-details">
      <summary>更多参数</summary>
      <div class="training-parameter-grid">
        <label class="field-label">优化器
          <select v-model="parameters.optimizer">
            <option value="auto">自动</option><option value="SGD">SGD</option><option value="Adam">Adam</option><option value="AdamW">AdamW</option>
          </select>
        </label>
        <label class="field-label">提前停止 patience<input v-model.number="parameters.patience" type="number" min="0" max="1000" /><small>连续多少轮没有提升就结束，0 表示不提前停止</small></label>
        <label class="field-label">读图进程 workers<input v-model.number="parameters.workers" type="number" min="0" max="32" /></label>
        <label class="field-label">随机种子 seed<input v-model.number="parameters.seed" type="number" min="0" /></label>
        <label class="field-label checkbox-label"><input v-model="parameters.cos_lr" type="checkbox" />余弦学习率衰减</label>
      </div>
    </details>
    <label class="field-label">名称（可选）<input v-model="runName" maxlength="120" :placeholder="selectedDataset ? `${selectedDataset.name} · ${model.replace('.pt', '')}` : '默认用“数据集 · 模型”命名'" /></label>
    <p v-if="formIssue" class="muted-copy">{{ formIssue }}</p>
    <button type="button" class="primary-button" :disabled="Boolean(formIssue) || submitting" @click="submit">{{ submitting ? '正在提交…' : '开始训练' }}</button>
  </section>

  <section class="section-block">
    <header class="section-heading"><div><h2>训练记录</h2><span class="count-badge">{{ runs.length }}</span></div></header>
    <div v-if="loading && !runs.length" class="empty-state">正在加载…</div>
    <div v-else-if="!runs.length" class="empty-state"><strong>还没有训练</strong><p>先上传数据集，再点右上角“新建训练”。</p></div>
    <ul v-else class="training-list">
      <li v-for="run in runs" :key="run.id">
        <RouterLink :to="`/training/${run.id}`" class="training-row">
          <span class="training-title">
            <strong>{{ run.name }}</strong>
            <small>{{ run.model.replace('.pt', '') }} · {{ datasetNames[run.dataset_id] ?? '已删除的数据集' }} · {{ nodeLabel(run.node_id, nodes) }} GPU {{ run.gpu_device_ids.join(', ') }}</small>
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
      <UploadDropzone compact accept=".zip,application/zip,application/x-zip-compressed" :multiple="false" :progress="uploadProgress" :disabled="uploadProgress !== null" title="拖入 YOLO 格式的数据集 ZIP，或点击选择" hint="images/ 与 labels/ 目录 + data.yaml（或 classes.txt）；没有验证集时自动划分" @files="importDataset" />
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
          <strong>{{ dataset.name }}</strong>
          <small>
            训练 {{ dataset.stats.splits.train.images }} 张 · 验证 {{ dataset.stats.splits.val.images }} 张{{ dataset.stats.auto_split ? '（自动划分）' : '' }}
            · {{ dataset.stats.splits.train.boxes + dataset.stats.splits.val.boxes }} 个标注框 · {{ formatBytes(dataset.size_bytes) }} · {{ formatDateTime(dataset.created_at) }}
          </small>
          <span class="class-chips">
            <span v-for="[label, count] in classCounts(dataset)" :key="label">{{ label }} <b>{{ count }}</b></span>
            <button v-if="dataset.class_names.length > CHIP_LIMIT" type="button" class="text-button" @click="toggleClasses(dataset.id)">
              {{ expanded.has(dataset.id) ? '收起' : `全部 ${dataset.class_names.length} 类` }}
            </button>
          </span>
        </div>
        <span class="toolbar-actions">
          <button type="button" class="secondary-button compact-button" @click="startCreating(dataset.id)">用它训练</button>
          <button type="button" class="text-button" @click="removeDataset(dataset)">删除</button>
        </span>
      </li>
    </ul>
  </section>
</template>
