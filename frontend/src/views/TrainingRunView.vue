<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { systemApi } from '@/api/system'
import {
  activeTrainingStatuses, formatMetric, progressRatio, progressText, trainingApi, trainingStatusLabel,
  trainingStatusTone, type TrainingDataset, type TrainingRun,
} from '@/api/training'
import TrainingChart, { type ChartSeries } from '@/components/training/TrainingChart.vue'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime, formatDuration, nodeLabel } from '@/utils/presentation'

const route = useRoute()
const router = useRouter()
const run = ref<TrainingRun | null>(null)
const dataset = ref<TrainingDataset | null>(null)
const nodes = ref<Array<{ id: string; name: string }>>([])
const logs = ref<string[]>([])
const error = ref('')
const busy = ref(false)
const showTable = ref(false)
const followLogs = ref(true)
const logElement = ref<HTMLElement | null>(null)
const now = ref(Date.now())

const runId = computed(() => String(route.params.id))
const active = computed(() => Boolean(run.value && activeTrainingStatuses.includes(run.value.status)))
const epochs = computed(() => run.value?.parameters.epochs ?? 1)
const rows = computed(() => run.value?.metrics ?? [])
const percent = computed(() => {
  const ratio = run.value ? progressRatio(run.value) : null
  return ratio === null ? null : Math.round(ratio * 100)
})
const elapsedMs = computed(() => {
  if (!run.value?.started_at) return null
  const end = run.value.finished_at ? new Date(run.value.finished_at).getTime() : now.value
  return end - new Date(run.value.started_at).getTime()
})
const remaining = computed(() => {
  const ratio = run.value ? progressRatio(run.value) : null
  if (!active.value || !ratio || ratio < 0.02 || elapsedMs.value === null) return null
  return formatDuration((elapsedMs.value / ratio) * (1 - ratio))
})

const metricSeries = computed<ChartSeries[]>(() => [
  ['metrics/mAP50-95(B)', 'mAP50-95'],
  ['metrics/mAP50(B)', 'mAP50'],
  ['metrics/precision(B)', '精确率'],
  ['metrics/recall(B)', '召回率'],
].map(([key, label], index) => ({ label: label!, slot: index + 1, points: points(key!) })))

const lossLabels: Record<string, string> = { box: '定位损失 box', cls: '分类损失 cls', dfl: '分布损失 dfl' }
const lossCharts = computed(() => {
  const kinds = new Set<string>()
  for (const row of rows.value) {
    for (const key of Object.keys(row)) {
      const match = /^(?:train|val)\/(.+)_loss$/.exec(key)
      if (match) kinds.add(match[1]!)
    }
  }
  const order = ['box', 'cls', 'dfl']
  return [...kinds].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99)).map((kind) => ({
    title: lossLabels[kind] ?? `${kind} 损失`,
    series: [
      { label: '训练集', slot: 1, points: points(`train/${kind}_loss`) },
      { label: '验证集', slot: 2, points: points(`val/${kind}_loss`) },
    ],
  }))
})
const tableColumns = computed(() => {
  const keys = new Set<string>()
  for (const row of rows.value) Object.keys(row).forEach((key) => keys.add(key))
  keys.delete('epoch')
  return ['epoch', ...[...keys].filter((key) => !key.startsWith('lr/'))]
})
const images = computed(() => Object.keys(run.value?.artifacts ?? {}).filter((name) => /\.(png|jpe?g)$/i.test(name)))
const files = computed(() => Object.entries(run.value?.artifacts ?? {}).filter(([name]) => !images.value.includes(name)))
const imageTitles: Record<string, string> = {
  'results.png': '训练曲线总览', 'confusion_matrix_normalized.png': '归一化混淆矩阵', 'confusion_matrix.png': '混淆矩阵',
  'BoxPR_curve.png': 'PR 曲线', 'BoxF1_curve.png': 'F1 曲线', 'BoxP_curve.png': '精确率曲线', 'BoxR_curve.png': '召回率曲线',
  'labels.jpg': '标注分布', 'val_batch0_pred.jpg': '验证集预测样例', 'val_batch0_labels.jpg': '验证集标注样例',
}
const orderedImages = computed(() => {
  const order = Object.keys(imageTitles)
  return [...images.value].sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
})

function points(key: string): Array<[number, number]> {
  return rows.value.flatMap((row) => typeof row[key] === 'number' && typeof row.epoch === 'number' ? [[row.epoch, row[key]!] as [number, number]] : [])
}
function formatCell(key: string, value: number | undefined) {
  if (value === undefined) return '—'
  if (key === 'epoch') return String(value)
  if (key.startsWith('metrics/')) return formatMetric(value)
  if (key === 'time') return formatDuration(value * 1000)
  return value.toFixed(4)
}
const columnLabel = (key: string) => key === 'epoch' ? '轮次' : key === 'time' ? '累计用时' : key.replace('metrics/', '').replace('(B)', '')

async function load() {
  try {
    run.value = await trainingApi.run(runId.value)
    error.value = ''
  } catch (reason) {
    error.value = errorMessage(reason, '加载训练失败')
  }
}
async function loadLogs() {
  if (!run.value?.started_at) return
  try {
    logs.value = await trainingApi.logs(runId.value)
    if (followLogs.value) {
      await nextTick()
      if (logElement.value) logElement.value.scrollTop = logElement.value.scrollHeight
    }
  } catch { /* 日志读取失败时保留上一次的内容 */ }
}

let timer: number | undefined
function schedule() {
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    now.value = Date.now()
    if (active.value) { await load(); await loadLogs() }
    schedule()
  }, 3000)
}
onMounted(async () => {
  await load()
  await loadLogs()
  schedule()
  try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* 显示节点 ID */ }
  try { dataset.value = (await trainingApi.datasets()).find((item) => item.id === run.value?.dataset_id) ?? null } catch { /* 数据集名称可选 */ }
})
onBeforeUnmount(() => window.clearTimeout(timer))
watch(runId, async () => { await load(); await loadLogs() })

async function stop() {
  if (!run.value || !window.confirm('停止训练？已经保存的权重（last.pt、best.pt）会保留，当前这一轮的进度会丢失。')) return
  busy.value = true
  try {
    run.value = await trainingApi.stop(run.value.id)
    toast.info('正在停止训练')
  } catch (reason) {
    toast.error(errorMessage(reason, '停止失败'))
  } finally { busy.value = false }
}
async function remove() {
  if (!run.value || !window.confirm(`删除训练“${run.value.name}”以及它的权重和日志？`)) return
  busy.value = true
  try {
    await trainingApi.remove(run.value.id)
    toast.info('已删除训练记录')
    await router.push('/training')
  } catch (reason) {
    toast.error(errorMessage(reason, '删除失败'))
  } finally { busy.value = false }
}
const percentFormat = (value: number) => `${Math.round(value * 100)}%`
</script>

<template>
  <RouterLink class="back-link" to="/training">← 训练中心</RouterLink>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <template v-if="run">
    <header class="page-heading page-heading-actions">
      <div>
        <h1>{{ run.name }}</h1>
        <p>
          <span :class="`task-status task-${trainingStatusTone[run.status]}`"><i class="status-dot" />{{ trainingStatusLabel[run.status] }}</span>
          {{ run.model.replace('.pt', '') }} · {{ dataset?.name ?? '数据集' }} · {{ nodeLabel(run.node_id, nodes) }} GPU {{ run.gpu_device_ids.join(', ') }}
        </p>
      </div>
      <span class="toolbar-actions">
        <button v-if="active" type="button" class="secondary-button danger-button" :disabled="busy || run.status === 'stopping'" @click="stop">{{ run.status === 'stopping' ? '正在停止…' : '停止训练' }}</button>
        <a v-if="Object.keys(run.artifacts).length" class="secondary-button" :href="trainingApi.archiveUrl(run.id)" download>下载全部结果</a>
        <button v-if="!active" type="button" class="text-button" :disabled="busy" @click="remove">删除</button>
      </span>
    </header>

    <div v-if="run.status === 'failed'" class="alert" role="alert"><strong>训练失败</strong><p>{{ run.error }}</p></div>

    <section class="section-block training-status-panel" aria-label="训练进度">
      <div class="training-progress-line">
        <strong>{{ progressText(run) || (run.status === 'queued' ? '等待调度' : trainingStatusLabel[run.status]) }}</strong>
        <span class="muted-copy">
          <template v-if="elapsedMs !== null">已用时 {{ formatDuration(elapsedMs) }}</template>
          <template v-if="remaining"> · 预计还需 {{ remaining }}</template>
        </span>
      </div>
      <progress v-if="percent !== null" :value="percent" max="100" aria-label="训练进度" />
      <dl class="stat-tiles">
        <div><dt>最佳 mAP50-95</dt><dd>{{ formatMetric(run.best?.map50_95) }}</dd><small v-if="run.best">第 {{ run.best.epoch }} 轮</small></div>
        <div><dt>mAP50</dt><dd>{{ formatMetric(run.best?.map50) }}</dd></div>
        <div><dt>精确率</dt><dd>{{ formatMetric(run.best?.precision) }}</dd></div>
        <div><dt>召回率</dt><dd>{{ formatMetric(run.best?.recall) }}</dd></div>
      </dl>
    </section>

    <section class="section-block">
      <header class="section-heading">
        <div><h2>训练曲线</h2></div>
        <button type="button" class="text-button" :aria-pressed="showTable" @click="showTable = !showTable">{{ showTable ? '查看曲线' : '查看数据表' }}</button>
      </header>
      <div v-if="!showTable" class="training-charts">
        <TrainingChart class="chart-wide" title="验证集指标" :series="metricSeries" :epochs="epochs" :y-max="1" :format="percentFormat" :height="220" />
        <TrainingChart v-for="chart in lossCharts" :key="chart.title" :title="chart.title" :series="chart.series" :epochs="epochs" />
      </div>
      <div v-else class="table-scroll">
        <table class="data-table">
          <thead><tr><th v-for="key in tableColumns" :key="key" scope="col">{{ columnLabel(key) }}</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.epoch"><td v-for="key in tableColumns" :key="key">{{ formatCell(key, row[key]) }}</td></tr>
            <tr v-if="!rows.length"><td :colspan="tableColumns.length">第一轮结束后显示数据</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="training-detail-grid">
      <section class="section-block">
        <header class="section-heading">
          <div><h2>日志</h2></div>
          <label class="checkbox-label"><input v-model="followLogs" type="checkbox" />自动滚动</label>
        </header>
        <pre ref="logElement" class="training-logs" tabindex="0" aria-label="训练日志">{{ logs.length ? logs.join('\n') : (run.started_at ? '暂无日志' : '训练容器启动后显示日志') }}</pre>
      </section>

      <section class="section-block">
        <header class="section-heading"><div><h2>训练配置</h2></div></header>
        <dl class="definition-list">
          <dt>数据集</dt><dd>{{ dataset ? `${dataset.name}（${dataset.class_names.length} 类）` : '—' }}</dd>
          <dt>预训练模型</dt><dd>{{ run.model }}</dd>
          <dt>轮数 / batch</dt><dd>{{ run.parameters.epochs }} / {{ run.parameters.batch === -1 ? '自动' : run.parameters.batch }}</dd>
          <dt>图片尺寸</dt><dd>{{ run.parameters.imgsz }}</dd>
          <dt>学习率</dt><dd>{{ run.parameters.lr0 }}{{ run.parameters.cos_lr ? '（余弦衰减）' : '' }} · {{ run.parameters.optimizer === 'auto' ? '自动选择优化器' : run.parameters.optimizer }}</dd>
          <dt>提前停止</dt><dd>{{ run.parameters.patience ? `${run.parameters.patience} 轮无提升` : '关闭' }}</dd>
          <dt>提交</dt><dd>{{ run.created_by }} · {{ formatDateTime(run.created_at) }}</dd>
          <dt>结束</dt><dd>{{ formatDateTime(run.finished_at) }}</dd>
        </dl>
        <template v-if="files.length">
          <h3>下载</h3>
          <ul class="artifact-list">
            <li v-for="[name, size] in files" :key="name"><a :href="trainingApi.artifactUrl(run.id, name)" download>{{ name }}</a><small>{{ formatBytes(size) }}</small></li>
          </ul>
        </template>
      </section>
    </div>

    <section v-if="orderedImages.length" class="section-block">
      <header class="section-heading"><div><h2>评估图表</h2></div><small class="muted-copy">训练结束时用最佳权重在验证集上生成</small></header>
      <div class="training-figures">
        <figure v-for="name in orderedImages" :key="name">
          <a :href="trainingApi.artifactUrl(run.id, name)" target="_blank" rel="noopener"><img :src="trainingApi.artifactUrl(run.id, name)" :alt="imageTitles[name] ?? name" loading="lazy" /></a>
          <figcaption>{{ imageTitles[name] ?? name }}</figcaption>
        </figure>
      </div>
    </section>
  </template>
</template>
