<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { trainingApi, defaultSplitConfig, type TrainingDataset } from '@/api/training'
import DatasetSplitForm from '@/components/training/DatasetSplitForm.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime } from '@/utils/presentation'
const datasets = ref<TrainingDataset[]>([])
const loading = ref(false)
const error = ref('')
const importing = ref(false)
const query = ref('')
const page = ref(1)
const pageSize = 10
const filtered = computed(() => datasets.value.filter((item) => item.name.toLowerCase().includes(query.value.trim().toLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(query, () => { page.value = 1 })
watch(pages, (value) => { page.value = Math.min(page.value, value) })
async function refresh() {
  loading.value = true
  try { datasets.value = await trainingApi.datasets(); error.value = '' }
  catch (reason) { error.value = errorMessage(reason, '加载数据集失败') }
  finally { loading.value = false }
}
onMounted(refresh)
// 上传数据集
const uploadingName = ref('')
const uploadParent = ref('')
const splitConfig = ref({ ...defaultSplitConfig })
const splitForm = ref<InstanceType<typeof DatasetSplitForm> | null>(null)
const uploadProgress = ref<number | null>(null)
const lastImported = ref<TrainingDataset | null>(null)

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

</script>
<template>
  <header class="page-heading page-heading-actions"><div><h1>训练数据集</h1><p>保存训练用图片与标注，管理校验结果和版本；新建训练时选择其中一个版本。</p></div>
    <button type="button" class="primary-button compact-button" :disabled="uploadProgress !== null" @click="importing = !importing">{{ importing ? '收起导入' : '导入数据集' }}</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }} <button class="text-button" @click="refresh">重试</button></p>
  <section v-show="importing" class="section-block" aria-label="导入训练数据集">
    <h2>导入图片与标注</h2>
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
  </section>
  <section class="section-block" aria-label="数据集版本列表">
    <header class="section-heading"><div><h2>数据集版本</h2><span class="count-badge">{{ datasets.length }}</span></div><input v-model="query" class="training-search" type="search" aria-label="搜索数据集" placeholder="搜索数据集名称" /></header>
    <p v-if="loading && !datasets.length" class="empty-state">正在读取数据集…</p>
    <div v-else-if="!datasets.length" class="empty-state"><strong>还没有训练数据集</strong><p>导入包含图片和标注的 ZIP，校验完成后即可用于训练。</p><button class="secondary-button compact-button" @click="importing = true">导入数据集</button></div>
    <p v-else-if="!filtered.length" class="empty-state">没有匹配的数据集。</p>
    <ul v-else class="dataset-list">
      <li v-for="dataset in visible" :key="dataset.id">
        <div><strong>{{ dataset.name }} · v{{ dataset.version ?? 1 }}</strong>
          <small>{{ dataset.class_names.length }} 类 · 训练 {{ dataset.stats.splits.train.images }} 张 · 验证 {{ dataset.stats.splits.val.images }} 张 · 测试 {{ dataset.stats.splits.test?.images ?? 0 }} 张</small>
          <small>{{ formatBytes(dataset.size_bytes) }} · {{ formatDateTime(dataset.created_at) }}</small>
        </div>
        <span class="toolbar-actions">
          <RouterLink :to="`/training/datasets/${dataset.id}`" class="secondary-button compact-button">详情与版本</RouterLink>
          <RouterLink :to="{ path: '/training/new', query: { dataset: dataset.id } }" class="secondary-button compact-button">用此版本训练</RouterLink>
          <button type="button" class="text-button" @click="removeDataset(dataset)">删除</button>
        </span>
      </li>
    </ul>
    <nav v-if="pages > 1" class="training-pagination" aria-label="数据集分页"><button class="secondary-button compact-button" :disabled="page === 1" @click="page--">上一页</button><span>{{ page }} / {{ pages }}</span><button class="secondary-button compact-button" :disabled="page === pages" @click="page++">下一页</button></nav>
  </section>
</template>
