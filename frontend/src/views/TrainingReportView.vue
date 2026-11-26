<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { trainingApi, type TrainingReport } from '@/api/training'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/presentation'
const route = useRoute()
const report = ref<TrainingReport | null>(null)
const error = ref('')
const loading = ref(false)
async function load() {
  loading.value = true
  error.value = ''
  try { report.value = await trainingApi.report(String(route.params.id)) }
  catch (reason) { error.value = errorMessage(reason, '报告读取失败') }
  finally { loading.value = false }
}
onMounted(load)
watch(() => route.params.id, () => { report.value = null; void load() })
const metrics: Record<string, string> = { 'metrics/precision(B)': 'Precision', 'metrics/recall(B)': 'Recall', 'metrics/mAP50(B)': 'mAP50', 'metrics/mAP50-95(B)': 'mAP50-95' }
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '未记录'
const json = (value: unknown) => JSON.stringify(value, null, 2)
</script>

<template>
  <RouterLink class="back-link" :to="`/training/${route.params.id}`">← 训练详情</RouterLink>
  <header class="page-heading page-heading-actions">
    <div><h1>完整训练报告</h1><p>{{ report?.name ?? '读取报告' }}</p></div>
    <div class="toolbar-actions">
      <button class="secondary-button compact-button" :disabled="loading" @click="load">刷新快照</button>
      <template v-if="report">
        <a class="primary-button compact-button" :href="trainingApi.downloadUrl(report.id, 'report.pdf')" download>下载 PDF</a>
        <a class="secondary-button compact-button" :href="trainingApi.downloadUrl(report.id, 'report.json')" download>报告 JSON</a>
        <a class="secondary-button compact-button" :href="trainingApi.archiveUrl(report.id)" download>完整产物 ZIP</a>
      </template>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <p v-if="loading && !report" class="muted-copy">正在生成报告快照…</p>
  <template v-if="report">
    <section class="section-block">
      <h2>{{ report.status_label }} · {{ report.algorithm === 'fasterrcnn' ? 'Faster R-CNN' : report.algorithm.toUpperCase() }} / {{ report.scale ?? '规格未记录' }}</h2>
      <p class="muted-copy">快照 {{ formatDateTime(report.snapshot_at) }} · 指标以 0–1 原始值展示，与下载数据一致。</p>
      <p v-for="note in report.notes" :key="note" class="build-notice">{{ note }}</p>
      <p v-if="report.error" class="alert">{{ report.error }}</p>
      <dl class="definition-list">
        <dt>任务 ID</dt><dd>{{ report.id }}</dd>
        <dt>训练方式</dt><dd>{{ report.operation === 'val' ? '独立验证' : report.training_mode === 'resume' ? '断点续训' : report.training_mode === 'scratch' ? '从头训练' : '权重微调' }}</dd>
        <dt>初始权重</dt><dd>{{ report.source.label ?? '未记录' }}</dd>
        <dt>权重哈希</dt><dd>{{ report.source.sha256 ?? '未记录' }}</dd>
        <dt>数据版本</dt><dd>{{ report.dataset.name }} · v{{ report.dataset.version ?? '未记录' }}</dd>
        <dt>数据内容哈希</dt><dd>{{ report.dataset.sha256 ?? '未记录' }}</dd>
        <dt>训练设备</dt><dd>{{ report.node_id }} · {{ report.parameters.device === 'cpu' ? 'CPU' : `GPU ${report.gpu_device_ids.join(', ')}` }}</dd>
        <dt>创建 / 开始 / 结束</dt><dd>{{ formatDateTime(report.created_at) }} / {{ formatDateTime(report.started_at) }} / {{ formatDateTime(report.finished_at) }}</dd>
      </dl>
    </section>
    <section class="section-block">
      <h2>逐轮验证结果</h2>
      <p class="muted-copy">最佳轮次按 mAP50-95 选择。best.pt 的最终评估列在下方，可能与逐轮记录存在差异。</p>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>记录</th><th>轮次</th><th v-for="label in metrics" :key="label">{{ label }}</th></tr></thead>
        <tbody><tr v-for="[label, row] in [['最佳轮次', report.best], ['最终轮次', report.last]] as const" :key="label">
          <td>{{ label }}</td><td>{{ row?.epoch ?? '未记录' }}</td><td v-for="(_, key) in metrics" :key="key">{{ number(row?.[key]) }}</td>
        </tr></tbody>
      </table></div>
      <div class="report-downloads"><a :href="trainingApi.downloadUrl(report.id, 'metrics.csv')" download>轮次 CSV</a><a :href="trainingApi.downloadUrl(report.id, 'metrics.json')" download>轮次 JSON</a></div>
      <div class="training-figures"><figure v-for="name in report.charts" :key="name"><img :src="trainingApi.chartUrl(report.id, name)" :alt="name" loading="lazy" /><figcaption><a :href="trainingApi.chartUrl(report.id, name)" :download="`${name}.png`">下载 {{ name }} PNG</a></figcaption></figure></div>
      <details class="inline-details"><summary>全部轮次指标</summary><div class="table-scroll"><table class="data-table"><thead><tr><th>轮次</th><th v-for="label in metrics" :key="label">{{ label }}</th></tr></thead><tbody><tr v-for="(row, i) in report.metrics" :key="i"><td>{{ row.epoch }}</td><td v-for="(_, key) in metrics" :key="key">{{ number(row[key]) }}</td></tr></tbody></table></div></details>
    </section>
    <section v-if="report.evaluation" class="section-block">
      <h2>{{ report.operation === 'val' ? '独立验证' : '最佳权重最终评估' }} · {{ report.evaluation.split }}</h2>
      <p v-if="report.evaluation.confusion_matrix_note" class="muted-copy">{{ report.evaluation.confusion_matrix_note }}</p>
      <p class="muted-copy mono">权重 SHA256 {{ report.evaluation.weight_sha256 }}</p>
      <dl class="definition-list"><template v-for="(value, key) in report.evaluation.metrics" :key="key"><dt>{{ key }}</dt><dd>{{ number(value) }}</dd></template></dl>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>类别</th><th>实例</th><th>P</th><th>R</th><th>F1</th><th>mAP50</th><th>mAP50-95</th></tr></thead><tbody><tr v-for="item in report.evaluation.per_class" :key="item.class_id"><td>{{ item.name }}</td><td>{{ item.instances }}</td><td v-for="key in ['precision', 'recall', 'f1', 'map50', 'map50_95'] as const" :key="key">{{ number(item[key]) }}</td></tr></tbody></table></div>
      <div class="report-downloads"><a :href="trainingApi.downloadUrl(report.id, 'per-class.csv')" download>类别 CSV</a><a :href="trainingApi.downloadUrl(report.id, 'per-class.json')" download>类别 JSON</a></div>
    </section>
    <section class="section-block">
      <h2>图表与预测样例</h2>
      <a :href="trainingApi.downloadUrl(report.id, 'figures.zip')" download>批量下载 PNG 图表</a>
      <p v-if="!report.figures.length" class="muted-copy">尚无评估图表或样例图片。</p>
      <div class="training-figures"><figure v-for="name in report.figures" :key="name"><img :src="trainingApi.artifactUrl(report.id, name)" :alt="name" loading="lazy" /><figcaption>{{ name }} · <a :href="trainingApi.figureUrl(report.id, name)" :download="`${name}.png`">PNG</a></figcaption></figure></div>
    </section>
    <section class="section-block">
      <h2>配置与来源</h2>
      <details open class="inline-details"><summary>实际生效配置与提交参数</summary><pre class="annotation-example">{{ json(report.configuration) }}</pre></details>
      <details class="inline-details"><summary>数据版本和划分</summary><pre class="annotation-example">{{ json(report.dataset) }}</pre></details>
      <details class="inline-details"><summary>权重来源</summary><pre class="annotation-example">{{ json(report.source) }}</pre></details>
      <h3>产物</h3><ul class="artifact-list"><li v-for="(size, name) in report.artifacts" :key="name"><a :href="trainingApi.artifactUrl(report.id, String(name))" download>{{ name }}</a><small>{{ size }} bytes</small></li></ul>
      <p class="muted-copy">{{ report.logs_note }}</p><a :href="trainingApi.downloadUrl(report.id, 'logs.txt')" download>下载日志</a>
    </section>
  </template>
</template>
