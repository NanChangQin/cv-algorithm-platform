<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { systemApi } from '@/api/system'
import {
  activeTrainingStatuses, formatMetric, progressRatio, progressText, trainingApi, trainingStatusLabel,
  trainingStatusTone, type TrainingDataset, type TrainingRun,
  type EvaluationReport,
} from '@/api/training'
import TrainingChart, { type ChartSeries } from '@/components/training/TrainingChart.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime, formatDuration, nodeLabel } from '@/utils/presentation'

const route = useRoute()
const router = useRouter()
const run = shallowRef<TrainingRun | null>(null)
const evaluation = shallowRef<EvaluationReport | null>(null)
const isValidation = computed(() => run.value?.progress?.operation === 'val')
const dataset = ref<TrainingDataset | null>(null)
const nodes = ref<Array<{ id: string; name: string }>>([])
const logs = ref<string[]>([])
const error = ref('')
const busy = ref(false)
const showTable = ref(false)
const followLogs = ref(true)
const logElement = ref<HTMLElement | null>(null)
const now = ref(Date.now())

const { algorithms, refresh: refreshAlgorithms } = useAlgorithms()
const runId = computed(() => String(route.params.id))

// ---- 发布为推理模型 ----
const publishing = ref(false)
const publishBusy = ref(false)
const checkpoint = ref<'best' | 'last'>('best')
const modelKey = ref('')
const modelName = ref('')
const modelVersion = ref('')
const modelDescription = ref('')
const published = computed(() => algorithms.value.filter((item) => item.lineage?.training_run_id === runId.value))
const checkpoints = computed(() => (['best', 'last'] as const).filter((name) => run.value?.artifacts[`weights/${name}.pt`] !== undefined))
const canPublish = computed(() => Boolean(run.value && ['completed', 'stopped'].includes(run.value.status) && checkpoints.value.length))
const lastRow = computed(() => rows.value[rows.value.length - 1])
const keyIssue = computed(() => /^[a-z0-9][a-z0-9-]{2,63}$/.test(modelKey.value) ? '' : '模型 ID 用小写字母、数字和连字符，3 到 64 个字符')
function startPublishing() {
  if (!run.value) return
  publishing.value = true
  checkpoint.value = checkpoints.value[0] ?? 'best'
  // 同一次训练再次发布时沿用已有的模型 ID，版本号自动递增
  modelKey.value = published.value[0]?.key ?? `${run.value.model.replace('.pt', '')}-${run.value.id.slice(0, 6)}`
  modelName.value = published.value[0]?.name ?? run.value.name
  modelVersion.value = ''
  modelDescription.value = ''
}
async function publish() {
  if (!run.value || keyIssue.value || publishBusy.value) return
  publishBusy.value = true
  try {
    const model = await trainingApi.publish(run.value.id, {
      checkpoint: checkpoint.value, key: modelKey.value, name: modelName.value.trim(),
      version: modelVersion.value.trim(), description: modelDescription.value.trim(),
    })
    toast.success(`已发布 ${model.name} v${model.version}`)
    publishing.value = false
    await refreshAlgorithms()
  } catch (reason) {
    toast.error(errorMessage(reason, '发布失败'))
  } finally { publishBusy.value = false }
}
const active = computed(() => Boolean(run.value && activeTrainingStatuses.includes(run.value.status)))
const configuration = computed(() => run.value?.progress?.configuration ?? { normalized: run.value?.parameters })
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

const lossLabels: Record<string, string> = { box: '定位损失 box', cls: '分类损失 cls', dfl: '分布损失 dfl', l1: '回归损失 l1' }
const lossCharts = computed(() => {
  const kinds = new Set<string>()
  for (const row of rows.value) {
    for (const key of Object.keys(row)) {
      const match = /^(?:train|val)\/(.+)_loss$/.exec(key)
      if (match) kinds.add(match[1]!)
    }
  }
  const order = ['box', 'cls', 'dfl', 'l1']
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
const imageTitles: Record<string, string> = {
  'results.png': '训练曲线总览', 'confusion_matrix_normalized.png': '归一化混淆矩阵', 'confusion_matrix.png': '混淆矩阵',
  'BoxPR_curve.png': 'PR 曲线', 'BoxF1_curve.png': 'F1 曲线', 'BoxP_curve.png': '精确率曲线', 'BoxR_curve.png': '召回率曲线',
  'labels.jpg': '标注分布', 'val_batch0_pred.jpg': '验证集预测样例', 'val_batch0_labels.jpg': '验证集标注样例',
}
// 只展示认识的评估图表；训练批次样例等其余图片放在下载列表里
const images = computed(() => Object.keys(run.value?.artifacts ?? {}).filter((name) => name in imageTitles))
const files = computed(() => Object.entries(run.value?.artifacts ?? {}).filter(([name]) => !images.value.includes(name)))
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
    if (run.value.progress?.operation === 'val' && run.value.artifacts['evaluation.json'] !== undefined) evaluation.value = await trainingApi.evaluation(runId.value)
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
        <button v-if="canPublish && !publishing" type="button" class="primary-button compact-button" @click="startPublishing">发布为推理模型</button>
        <RouterLink v-if="!active && checkpoints.length" class="secondary-button" :to="{ path: '/exports', query: { run: run.id, checkpoint: checkpoints[0] } }">导出 ONNX</RouterLink>
        <a v-if="Object.keys(run.artifacts).length" class="secondary-button" :href="trainingApi.archiveUrl(run.id)" download>下载全部结果</a>
        <button v-if="!active" type="button" class="text-button" :disabled="busy" @click="remove">删除</button>
      </span>
    </header>

    <div v-if="run.status === 'failed'" class="alert" role="alert"><strong>训练失败</strong><p>{{ run.error }}</p></div>
    <section class="section-block" aria-label="模型来源与恢复状态">
      <h2>模型来源与恢复状态</h2>
      <p>{{ run.progress?.source?.mode === 'resume' ? '断点续训' : '权重初始化' }} · {{ run.progress?.source?.label ?? run.model }}</p>
      <p v-if="run.progress?.source?.sha256" class="muted-copy mono" style="overflow-wrap: anywhere">输入 SHA256 {{ run.progress.source.sha256 }}</p>
      <p v-if="isValidation" class="muted-copy">独立模型评估 · 数据划分 {{ run.parameters.split ?? 'val' }}</p>
      <p v-else-if="run.progress?.checkpoint">已保存第 {{ run.progress.checkpoint.epoch }}/{{ run.progress.checkpoint.epochs }} 轮的完整恢复状态。{{ run.progress.checkpoint.resumable ? '可以从下一轮继续原计划。' : run.progress.checkpoint.reason }}</p>
      <p v-else class="muted-copy">{{ active ? '完成第一轮且启用保存后生成恢复 checkpoint。' : '没有完整恢复 checkpoint；历史权重可用于微调。' }}</p>
      <div v-if="!active" class="toolbar-actions">
        <RouterLink v-if="run.progress?.checkpoint?.resumable && run.artifacts[run.progress.checkpoint.file] !== undefined" class="primary-button compact-button" :to="{ path: '/training', query: { resume: run.id } }">断点续训</RouterLink>
        <RouterLink v-for="name in checkpoints" :key="name" class="secondary-button compact-button" :to="{ path: '/training', query: { finetune: run.id, checkpoint: name } }">用 {{ name }} 权重微调</RouterLink>
        <RouterLink v-for="name in checkpoints" :key="`val-${name}`" class="secondary-button compact-button" :to="{ path: '/training', query: { validate: run.id, checkpoint: name } }">验证 {{ name }} 权重</RouterLink>
      </div>
    </section>

    <section v-if="publishing" class="section-block training-form publish-panel" aria-labelledby="publish-title">
      <header class="section-heading"><div><h2 id="publish-title">发布为推理模型</h2></div><button type="button" class="text-button" @click="publishing = false">取消</button></header>
      <p class="muted-copy">发布后成为算法库里的一个可用版本，使用通用 YOLO 推理镜像加载这份权重，记录数据集、训练参数和指标；部署时权重会自动同步到目标服务器。</p>
      <div class="checkpoint-options" role="radiogroup" aria-label="选择权重">
        <label v-if="checkpoints.includes('best')" :class="['model-option', { selected: checkpoint === 'best' }]">
          <input v-model="checkpoint" type="radio" value="best" />
          <strong>best.pt</strong><small>验证集最好的一轮<template v-if="run.best">（第 {{ run.best.epoch }} 轮，mAP50-95 {{ formatMetric(run.best.map50_95) }}）</template></small>
        </label>
        <label v-if="checkpoints.includes('last')" :class="['model-option', { selected: checkpoint === 'last' }]">
          <input v-model="checkpoint" type="radio" value="last" />
          <strong>last.pt</strong><small>最后一轮<template v-if="lastRow">（第 {{ lastRow.epoch }} 轮，mAP50-95 {{ formatMetric(lastRow['metrics/mAP50-95(B)']) }}）</template></small>
        </label>
      </div>
      <div class="publish-grid">
        <label class="field-label">模型 ID<input v-model.trim="modelKey" maxlength="64" /><small>同一 ID 的版本可以对比和回滚</small></label>
        <label class="field-label">显示名称<input v-model="modelName" maxlength="128" /></label>
        <label class="field-label">版本号<input v-model="modelVersion" maxlength="64" placeholder="自动递增，例如 1.0.1" /></label>
        <label class="field-label">说明（可选）<input v-model="modelDescription" maxlength="1000" /></label>
      </div>
      <p v-if="keyIssue" class="muted-copy">{{ keyIssue }}</p>
      <button type="button" class="primary-button" :disabled="Boolean(keyIssue) || publishBusy" @click="publish">{{ publishBusy ? '正在发布…' : '发布' }}</button>
    </section>

    <section v-if="published.length" class="section-block" aria-labelledby="published-title">
      <header class="section-heading"><div><h2 id="published-title">已发布的模型</h2><span class="count-badge">{{ published.length }}</span></div></header>
      <ul class="published-list">
        <li v-for="model in published" :key="model.id">
          <div>
            <strong>{{ model.name }} v{{ model.version }}</strong>
            <small>{{ model.key }} · {{ model.lineage?.checkpoint }}.pt · mAP50-95 {{ formatMetric(model.lineage?.metrics.map50_95) }}</small>
          </div>
          <span class="toolbar-actions">
            <RouterLink class="primary-button compact-button" :to="{ path: '/deployments', query: { algorithm: model.id } }">部署到服务器</RouterLink>
            <RouterLink class="secondary-button compact-button" :to="{ path: '/', query: { algorithm: model.id } }">在工作台试用</RouterLink>
            <RouterLink class="text-button" :to="{ path: '/algorithms', query: { selected: model.id } }">版本对比</RouterLink>
          </span>
        </li>
      </ul>
    </section>

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

    <section v-if="evaluation" class="section-block" aria-label="独立验证报告">
      <header class="section-heading"><div><h2>独立验证报告 · {{ evaluation.split }}</h2></div><a :href="trainingApi.artifactUrl(run.id, 'evaluation.json')" download>下载 JSON 报告</a></header>
      <p class="muted-copy mono" style="overflow-wrap: anywhere">模型 SHA256 {{ evaluation.weight_sha256 }}</p>
      <p class="muted-copy">每张图耗时：<span v-for="(value, key) in evaluation.speed" :key="key">{{ key }} {{ value.toFixed(3) }} ms · </span></p>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>类别</th><th>实例数</th><th>Precision</th><th>Recall</th><th>F1</th><th>mAP50</th><th>mAP50-95</th></tr></thead>
        <tbody><tr v-for="item in evaluation.per_class" :key="item.class_id"><td>{{ item.class_id }} · {{ item.name }}</td><td>{{ item.instances }}</td><td>{{ formatMetric(item.precision) }}</td><td>{{ formatMetric(item.recall) }}</td><td>{{ formatMetric(item.f1) }}</td><td>{{ formatMetric(item.map50) }}</td><td>{{ formatMetric(item.map50_95) }}</td></tr></tbody>
      </table></div>
      <details v-if="evaluation.confusion_matrix" class="inline-details"><summary>混淆矩阵原始数值（最后一类为背景）</summary><pre class="annotation-example">{{ JSON.stringify(evaluation.confusion_matrix, null, 2) }}</pre></details>
    </section>

    <section v-if="!isValidation" class="section-block">
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
          <dt>数据集</dt><dd><RouterLink :to="`/training/datasets/${run.dataset_id}`">{{ dataset ? `${dataset.name} · v${dataset.version ?? 1}（${dataset.class_names.length} 类）` : run.progress?.dataset?.name ?? '查看版本' }}</RouterLink></dd>
          <template v-if="run.progress?.dataset"><dt>数据版本 / 内容哈希</dt><dd class="mono">v{{ run.progress.dataset.version }} · {{ run.progress.dataset.sha256 }}</dd><dt>数据划分快照</dt><dd>{{ JSON.stringify(run.progress.dataset.split_config) }}</dd></template>
          <dt>预训练模型</dt><dd>{{ run.model }}</dd>
          <dt>{{ isValidation ? '数据划分 / batch' : '轮数 / batch' }}</dt><dd>{{ isValidation ? run.parameters.split ?? 'val' : run.parameters.epochs }} / {{ run.parameters.batch === -1 ? '自动' : run.parameters.batch }}</dd>
          <dt>图片尺寸</dt><dd>{{ run.parameters.imgsz }}</dd>
          <template v-if="!isValidation"><dt>学习率</dt><dd>{{ run.parameters.lr0 }}{{ run.parameters.cos_lr ? '（余弦衰减）' : '' }} · {{ run.parameters.optimizer === 'auto' ? '自动选择优化器' : run.parameters.optimizer }}</dd>
            <dt>提前停止</dt><dd>{{ run.parameters.patience ? `${run.parameters.patience} 轮无提升` : '关闭' }}</dd></template>
          <dt>提交</dt><dd>{{ run.created_by }} · {{ formatDateTime(run.created_at) }}</dd>
          <dt>结束</dt><dd>{{ formatDateTime(run.finished_at) }}</dd>
        </dl>
        <details class="inline-details"><summary>提交参数与实际生效配置</summary>
          <p class="muted-copy">运行器启动后回传 effective；自动批大小、优化器和学习率以该快照为准。快照也包含在训练产物 parameter-resolution.json 中。</p>
          <pre class="annotation-example">{{ JSON.stringify(configuration, null, 2) }}</pre>
        </details>
        <template v-if="files.length">
          <h3>下载</h3>
          <ul class="artifact-list">
            <li v-for="[name, size] in files" :key="name"><a :href="trainingApi.artifactUrl(run.id, name)" download>{{ name }}</a><small>{{ formatBytes(size) }}</small></li>
          </ul>
        </template>
      </section>
    </div>

    <section v-if="orderedImages.length" class="section-block">
      <header class="section-heading"><div><h2>评估图表</h2></div><small class="muted-copy">{{ isValidation ? '使用选定模型与数据划分生成' : '训练结束时用最佳权重在验证集上生成' }}</small></header>
      <div class="training-figures">
        <figure v-for="name in orderedImages" :key="name">
          <a :href="trainingApi.artifactUrl(run.id, name)" target="_blank" rel="noopener"><img :src="trainingApi.artifactUrl(run.id, name)" :alt="imageTitles[name] ?? name" loading="lazy" /></a>
          <figcaption>{{ imageTitles[name] ?? name }}</figcaption>
        </figure>
      </div>
    </section>
  </template>
</template>
