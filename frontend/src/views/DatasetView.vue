<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { defaultSplitConfig, trainingApi, type DatasetImage, type TrainingDataset } from '@/api/training'
import DatasetSplitForm from '@/components/training/DatasetSplitForm.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime } from '@/utils/presentation'

const route = useRoute()
const router = useRouter()
const dataset = ref<TrainingDataset | null>(null)
const versions = ref<TrainingDataset[]>([])
const images = ref<DatasetImage[]>([])
const selected = ref<DatasetImage | null>(null)
const split = ref('')
const offset = ref(0)
const total = ref(0)
const error = ref('')
const imageError = ref('')
const loading = ref(false)
const creating = ref(false)
const showBoxes = ref(true)
const config = ref({ ...defaultSplitConfig, mode: 'reshuffle' as const })
const splitForm = ref<InstanceType<typeof DatasetSplitForm> | null>(null)
const splits = ['train', 'val', 'test'] as const
const family = computed(() => dataset.value ? versions.value.filter((v) => v.family_id === dataset.value!.family_id).sort((a, b) => b.version - a.version) : [])
let generation = 0
let pageGeneration = 0
async function load() {
  const request = ++generation
  ++pageGeneration
  dataset.value = null
  images.value = []
  selected.value = null
  split.value = ''
  offset.value = 0
  error.value = ''
  loading.value = true
  try {
    const [data, all] = await Promise.all([trainingApi.dataset(String(route.params.id)), trainingApi.datasets()])
    if (request !== generation) return
    dataset.value = data
    versions.value = all
    await loadImages()
  } catch (reason) {
    if (request === generation) error.value = errorMessage(reason, '数据集加载失败')
  } finally { if (request === generation) loading.value = false }
}
async function loadImages() {
  if (!dataset.value) return
  const request = ++pageGeneration
  imageError.value = ''
  images.value = []
  selected.value = null
  try {
    const page = await trainingApi.datasetImages(dataset.value.id, split.value, offset.value)
    if (request !== pageGeneration) return
    images.value = page.items
    total.value = page.total
    selected.value = page.items[0] ?? null
  } catch (reason) {
    if (request === pageGeneration) imageError.value = errorMessage(reason, '图片加载失败')
  }
}
watch(() => route.params.id, load, { immediate: true })
watch(split, () => { offset.value = 0; void loadImages() })
async function page(delta: number) {
  offset.value += delta * 12
  await loadImages()
}
async function newVersion() {
  if (!dataset.value || creating.value || splitForm.value?.issue) return
  creating.value = true
  try {
    const next = await trainingApi.newDatasetVersion(dataset.value.id, config.value)
    toast.success(`已创建 v${next.version}，原版本保持不变`)
    await router.push(`/training/datasets/${next.id}`)
  } catch (reason) { toast.error(errorMessage(reason, '创建版本失败')) }
  finally { creating.value = false }
}
function chooseVersion(event: Event) {
  void router.push(`/training/datasets/${(event.target as HTMLSelectElement).value}`)
}
function color(index: number) { return ['#2563eb', '#dc2626', '#059669', '#9333ea', '#ca8a04'][index % 5] }
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><RouterLink to="/training">← 训练中心</RouterLink><h1>{{ dataset?.name ?? '数据集详情' }}<template v-if="dataset"> · v{{ dataset.version }}</template></h1><p>查看固定版本的数据、标注、划分和导入报告。</p></div>
    <RouterLink v-if="dataset" :to="{ path: '/training', query: { dataset: dataset.id } }" class="primary-button compact-button">用此版本训练</RouterLink>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }} <button class="text-button" @click="load">重试</button></p>
  <p v-if="loading" class="muted-copy">正在加载数据集…</p>
  <template v-if="dataset">
    <section class="section-block">
      <header class="section-heading"><div><h2>版本与来源</h2></div></header>
      <label class="field-label">数据集版本<select :value="dataset.id" aria-label="数据集版本" @change="chooseVersion"><option v-for="item in family" :key="item.id" :value="item.id">v{{ item.version }} · {{ formatDateTime(item.created_at) }}</option></select></label>
      <p class="muted-copy">{{ formatDateTime(dataset.created_at) }} · {{ dataset.created_by }} · {{ formatBytes(dataset.size_bytes) }}<template v-if="dataset.parent_id"> · <RouterLink :to="`/training/datasets/${dataset.parent_id}`">查看父版本</RouterLink></template></p>
      <dl class="dataset-provenance"><dt>规范化包 SHA256</dt><dd class="mono">{{ dataset.sha256 }}</dd><dt>原包 SHA256</dt><dd class="mono">{{ dataset.source_sha256 || '历史版本未保留原包' }}</dd><dt>划分配置</dt><dd>{{ Object.keys(dataset.split_config).length ? JSON.stringify(dataset.split_config) : '历史导入；沿用已有划分' }}</dd></dl>
      <div class="toolbar-actions">
        <a class="secondary-button compact-button" :href="trainingApi.datasetArchiveUrl(dataset.id)">下载规范化包</a>
        <a v-if="dataset.source_sha256" class="secondary-button compact-button" :href="trainingApi.datasetArchiveUrl(dataset.id, true)">下载原包</a>
        <a class="text-button" :href="trainingApi.datasetReportUrl(dataset.id)" target="_blank" rel="noopener">完整校验报告与图片索引（JSON）</a>
      </div>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>划分与类别统计</h2></div></header>
      <div class="dataset-table-scroll"><table class="dataset-table"><thead><tr><th>划分</th><th>图片</th><th>已标注</th><th>背景图</th><th>标注框</th></tr></thead><tbody><tr v-for="name in splits" :key="name"><th>{{ name }}</th><td>{{ dataset.stats.splits[name]?.images ?? 0 }}</td><td>{{ dataset.stats.splits[name]?.labeled ?? 0 }}</td><td>{{ dataset.stats.splits[name]?.backgrounds ?? 0 }}</td><td>{{ dataset.stats.splits[name]?.boxes ?? 0 }}</td></tr></tbody></table></div>
      <p class="muted-copy">test 保持独立，不参与训练与逐轮调参；缺失标注按背景图处理。</p>
      <p>重复图片组 {{ dataset.stats.duplicate_groups ?? '未检查' }} · 额外重复图片 {{ dataset.stats.duplicate_images ?? '未检查' }} · 跨划分泄漏组 {{ dataset.stats.leakage_groups ?? '未检查' }}</p>
      <ul v-if="dataset.stats.warnings.length" class="build-notice"><li v-for="warning in dataset.stats.warnings" :key="warning">{{ warning }}</li></ul>
      <details v-if="dataset.stats.invalid_examples.length"><summary>错误标注示例（错误行 {{ dataset.stats.invalid_lines }}）</summary><ul><li v-for="line in dataset.stats.invalid_examples" :key="line" class="mono">{{ line }}</li></ul></details>
      <details><summary>类别分布（{{ dataset.class_names.length }} 类）</summary><div class="dataset-table-scroll"><table class="dataset-table"><thead><tr><th>编号 / 类别</th><th>全部框数</th><th v-for="name in splits" :key="name">{{ name }}</th></tr></thead><tbody><tr v-for="(name, index) in dataset.class_names" :key="index"><th>{{ index }} / {{ name }}</th><td>{{ dataset.stats.instances[name] ?? 0 }}</td><td v-for="part in splits" :key="part">{{ dataset.stats.split_instances?.[part]?.[String(index)] ?? '—' }}</td></tr></tbody></table></div></details>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>图片与标注预览</h2></div><label><input v-model="showBoxes" type="checkbox" /> 显示标注框</label></header>
      <label class="field-label">查看划分<select v-model="split" aria-label="预览划分"><option value="">全部</option><option v-for="name in splits" :key="name">{{ name }}</option></select></label>
      <p v-if="imageError" class="alert" role="alert">{{ imageError }} <button class="text-button" @click="loadImages">重试</button></p>
      <div v-if="selected" class="dataset-preview">
        <svg :viewBox="`0 0 ${selected.width || 1} ${selected.height || 1}`" role="img" :aria-label="`${selected.source_path} 标注预览`">
          <image :href="trainingApi.datasetPreviewUrl(dataset.id, selected.id)" :width="selected.width || 1" :height="selected.height || 1" />
          <g v-if="showBoxes"><g v-for="(box, index) in selected.boxes" :key="index">
            <rect :x="(box.cx - box.width / 2) * selected.width" :y="(box.cy - box.height / 2) * selected.height" :width="box.width * selected.width" :height="box.height * selected.height" fill="none" :stroke="color(box.class_id)" stroke-width="2" vector-effect="non-scaling-stroke" />
          </g></g>
        </svg>
        <p>{{ selected.split }} · {{ selected.source_path }} · {{ selected.width }} × {{ selected.height }} · {{ selected.boxes.length }} 个框</p>
        <div class="class-chips"><span v-for="(box, i) in selected.boxes" :key="i" :style="{ color: color(box.class_id) }">{{ box.class_id }} {{ dataset.class_names[box.class_id] }}</span></div>
      </div>
      <div class="dataset-gallery"><button v-for="item in images" :key="item.id" type="button" :class="{ selected: selected?.id === item.id }" :aria-label="item.source_path" @click="selected = item"><img :src="trainingApi.datasetPreviewUrl(dataset.id, item.id)" :alt="item.source_path" loading="lazy" /><small>{{ item.split }} · {{ item.boxes.length }} 框</small></button></div>
      <p v-if="!images.length && !imageError" class="muted-copy">此划分没有图片。</p>
      <div class="toolbar-actions"><button class="secondary-button compact-button" :disabled="offset === 0" @click="page(-1)">上一页</button><span>{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + 12, total) }} / {{ total }}</span><button class="secondary-button compact-button" :disabled="offset + 12 >= total" @click="page(1)">下一页</button></div>
    </section>
    <section class="section-block">
      <h2>创建新的划分版本</h2>
      <p class="muted-copy">从保留的原包生成新版本，当前版本及其训练记录保持不变。需要更新图片或标注时，在训练中心上传 ZIP 并选择版本归属。</p>
      <template v-if="dataset.source_sha256"><DatasetSplitForm ref="splitForm" v-model="config" :disabled="creating" /><button class="primary-button" :disabled="creating || Boolean(splitForm?.issue)" @click="newVersion">{{ creating ? '正在校验并生成…' : '生成新版本' }}</button></template>
      <p v-else class="build-notice">历史版本未保留原包。请下载规范化包，在训练中心以新版本重新上传。</p>
    </section>
  </template>
</template>

<style scoped>
.dataset-provenance { display: grid; gap: 8px; }
.dataset-provenance dt { font-weight: 600; }
.dataset-provenance dd { margin: 0; overflow-wrap: anywhere; }
.dataset-table-scroll { overflow: auto; }
.dataset-table { width: 100%; border-collapse: collapse; margin: 12px 0; text-align: left; }
.dataset-table th, .dataset-table td { padding: 10px; border-bottom: 1px solid var(--border-color, #ddd); }
.dataset-preview { margin: 16px 0; }
.dataset-preview svg { display: block; max-height: 560px; width: 100%; background: #111827; }
.dataset-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 10px; margin: 16px 0; }
.dataset-gallery button { padding: 4px; border: 2px solid transparent; border-radius: 6px; background: transparent; cursor: pointer; color: inherit; }
.dataset-gallery button.selected { border-color: #2563eb; }
.dataset-gallery img { width: 100%; height: 90px; object-fit: contain; background: #111827; }
.dataset-gallery small { display: block; }
</style>
