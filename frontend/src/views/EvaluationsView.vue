<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { evaluationsApi, type CreatedDataset, type EvaluationDataset, type EvaluationRun, type EvaluationTaskType } from '@/api/evaluations'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { uploadFailureMessage, uploadInBatches } from '@/composables/useAssets'
import { toast } from '@/composables/useToast'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'
import { annotationExamples } from '@/utils/metrics'
import { formatDateTime, taskTypeLabel } from '@/utils/presentation'

const router = useRouter()
const { availableAlgorithms } = useAlgorithms()
const datasets = ref<EvaluationDataset[]>([])
const runs = ref<EvaluationRun[]>([])
const loading = ref(false)
const error = ref('')

// 新建评测集
const creating = ref(false)
const name = ref('')
const taskType = ref<EvaluationTaskType>('object_detection')
const images = ref<ImageAsset[]>([])
const uploadProgress = ref<number | null>(null)
const annotationFile = ref<File | null>(null)
const saving = ref(false)
const created = ref<CreatedDataset | null>(null)
const canCreate = computed(() => Boolean(name.value.trim() && images.value.length && annotationFile.value && !saving.value && uploadProgress.value === null))

// 运行评测
const runningDatasetId = ref('')
const chosenAlgorithms = ref<string[]>([])
const starting = ref(false)
const runningDataset = computed(() => datasets.value.find((item) => item.id === runningDatasetId.value))
const matchingAlgorithms = computed(() => availableAlgorithms.value.filter((item) => item.task_type === runningDataset.value?.task_type))

function algorithmNames(ids: string[]) {
  return ids.map((id) => availableAlgorithms.value.find((item) => item.id === id)?.name ?? '已删除的算法').join('、')
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    ;[datasets.value, runs.value] = await Promise.all([evaluationsApi.datasets(), evaluationsApi.runs()])
  } catch (reason) {
    error.value = errorMessage(reason, '加载评测数据失败')
  } finally { loading.value = false }
}
onMounted(refresh)

function startCreating() {
  creating.value = true
  created.value = null
  name.value = ''
  images.value = []
  annotationFile.value = null
}
async function handleImages(files: File[]) {
  uploadProgress.value = 0
  try {
    const outcome = await uploadInBatches(files, { onProgress: (ratio) => { uploadProgress.value = ratio } })
    const known = new Set(images.value.map((item) => item.id))
    images.value = [...images.value, ...outcome.uploaded.filter((item) => !known.has(item.id))]
    if (outcome.failed.length) toast.error(uploadFailureMessage(outcome.failed))
  } finally { uploadProgress.value = null }
}
function chooseAnnotations(event: Event) {
  annotationFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}
async function createDataset() {
  if (!canCreate.value || !annotationFile.value) return
  saving.value = true
  try {
    created.value = await evaluationsApi.createDataset({
      name: name.value.trim(),
      task_type: taskType.value,
      annotations_filename: annotationFile.value.name,
      annotations: await annotationFile.value.text(),
      asset_ids: images.value.map((item) => item.id),
    })
    toast.success(`已创建评测集，匹配 ${created.value.item_count} 张图片`)
    creating.value = false
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '创建评测集失败'))
  } finally { saving.value = false }
}
async function removeDataset(dataset: EvaluationDataset) {
  if (!window.confirm(`删除评测集“${dataset.name}”以及它的评测记录？图片和推理任务会保留。`)) return
  try {
    await evaluationsApi.deleteDataset(dataset.id)
    toast.info('已删除评测集')
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '删除失败'))
  }
}
function prepareRun(dataset: EvaluationDataset) {
  runningDatasetId.value = dataset.id
  chosenAlgorithms.value = availableAlgorithms.value.filter((item) => item.task_type === dataset.task_type).slice(0, 3).map((item) => item.id)
}
function toggleAlgorithm(id: string) {
  chosenAlgorithms.value = chosenAlgorithms.value.includes(id)
    ? chosenAlgorithms.value.filter((item) => item !== id)
    : [...chosenAlgorithms.value, id]
}
async function startRun() {
  if (!runningDataset.value || !chosenAlgorithms.value.length) return
  starting.value = true
  try {
    const run = await evaluationsApi.start({ dataset_id: runningDataset.value.id, algorithm_version_ids: chosenAlgorithms.value })
    toast.success(`已提交 ${run.task_count} 个推理任务`)
    await router.push(`/evaluations/${run.id}`)
  } catch (reason) {
    toast.error(errorMessage(reason, '启动评测失败'))
  } finally { starting.value = false }
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div>
      <h1>数据集评测</h1>
      <p>用带标注的图片衡量算法效果：上传图片和标注建立评测集，选择算法运行后自动计算精确率、召回率等指标。</p>
    </div>
    <button v-if="!creating" type="button" class="primary-button compact-button" @click="startCreating">新建评测集</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section v-if="creating" class="section-block evaluation-form">
    <header class="section-heading"><div><h2>新建评测集</h2></div><button type="button" class="text-button" @click="creating = false">取消</button></header>
    <div class="evaluation-form-grid">
      <label class="field-label">名称<input v-model="name" maxlength="120" placeholder="例如：园区夜间行人" /></label>
      <label class="field-label">任务类型
        <select v-model="taskType">
          <option v-for="(label, key) in taskTypeLabel" :key="key" :value="key">{{ label }}</option>
        </select>
      </label>
    </div>
    <h3>1. 上传图片</h3>
    <UploadDropzone compact :progress="uploadProgress" :disabled="uploadProgress !== null" title="拖入评测图片，或点击选择" hint="文件名要和标注里的一致" @files="handleImages" />
    <p v-if="images.length" class="muted-copy">已上传 {{ images.length }} 张图片 <button type="button" class="text-button" @click="images = []">清空</button></p>
    <h3>2. 选择标注文件</h3>
    <input type="file" :accept="annotationExamples[taskType].accept" aria-label="选择标注文件" @change="chooseAnnotations" />
    <pre class="annotation-example">{{ annotationExamples[taskType].text }}</pre>
    <button type="button" class="primary-button compact-button" :disabled="!canCreate" @click="createDataset">{{ saving ? '正在解析标注…' : '创建评测集' }}</button>
  </section>

  <div v-if="created" class="build-notice" role="status">
    <strong>“{{ created.name }}”已创建，匹配 {{ created.item_count }} 张图片。</strong>
    <p v-if="created.unmatched_files.length">标注里有 {{ created.unmatched_files.length }} 个文件名没有对应的图片：{{ created.unmatched_files.slice(0, 5).join('、') }}<template v-if="created.unmatched_files.length > 5"> 等</template></p>
    <p v-if="created.unlabeled_images.length">{{ created.unlabeled_images.length }} 张图片没有标注，已跳过：{{ created.unlabeled_images.slice(0, 5).join('、') }}<template v-if="created.unlabeled_images.length > 5"> 等</template></p>
  </div>

  <div class="evaluation-layout">
    <section class="section-block">
      <header class="section-heading"><div><h2>评测集</h2><span class="count-badge">{{ datasets.length }}</span></div></header>
      <div v-if="loading && !datasets.length" class="empty-state">正在加载…</div>
      <div v-else-if="!datasets.length" class="empty-state"><strong>还没有评测集</strong><p>点右上角“新建评测集”，上传图片和标注文件。</p></div>
      <ul v-else class="evaluation-list">
        <li v-for="dataset in datasets" :key="dataset.id" :class="{ selected: dataset.id === runningDatasetId }">
          <div>
            <strong>{{ dataset.name }}</strong>
            <small>{{ taskTypeLabel[dataset.task_type] }} · {{ dataset.item_count }} 张图片 · {{ formatDateTime(dataset.created_at) }}</small>
          </div>
          <span class="toolbar-actions">
            <button type="button" class="secondary-button compact-button" @click="prepareRun(dataset)">运行评测</button>
            <button type="button" class="text-button" @click="removeDataset(dataset)">删除</button>
          </span>
          <div v-if="dataset.id === runningDatasetId" class="evaluation-run-form">
            <p v-if="!matchingAlgorithms.length" class="muted-copy">没有可用的{{ taskTypeLabel[dataset.task_type] }}算法。</p>
            <template v-else>
              <span class="muted-copy">选择要评测的算法（每张图片、每个算法各运行一次，共 {{ dataset.item_count * chosenAlgorithms.length }} 个任务）</span>
              <label v-for="algorithm in matchingAlgorithms" :key="algorithm.id" class="evaluation-algorithm">
                <input type="checkbox" :checked="chosenAlgorithms.includes(algorithm.id)" @change="toggleAlgorithm(algorithm.id)" />
                {{ algorithm.name }} <small>v{{ algorithm.version }}</small>
              </label>
              <span class="toolbar-actions">
                <button type="button" class="primary-button compact-button" :disabled="!chosenAlgorithms.length || starting" @click="startRun">{{ starting ? '正在提交…' : '开始评测' }}</button>
                <button type="button" class="text-button" @click="runningDatasetId = ''">取消</button>
              </span>
            </template>
          </div>
        </li>
      </ul>
    </section>

    <section class="section-block">
      <header class="section-heading"><div><h2>评测记录</h2><span class="count-badge">{{ runs.length }}</span></div></header>
      <div v-if="!runs.length" class="empty-state"><strong>还没有评测记录</strong><p>在左侧评测集上点“运行评测”。</p></div>
      <ul v-else class="evaluation-list">
        <li v-for="run in runs" :key="run.id">
          <RouterLink :to="`/evaluations/${run.id}`">
            <strong>{{ run.dataset_name }}</strong>
            <small>{{ algorithmNames(run.algorithm_version_ids) }} · {{ run.task_count }} 个任务 · {{ formatDateTime(run.created_at) }}</small>
          </RouterLink>
          <span :class="`task-status task-${run.finished ? 'completed' : 'running'}`"><i class="status-dot" /> {{ run.finished ? '已完成' : '进行中' }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
