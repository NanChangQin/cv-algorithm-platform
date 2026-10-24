<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { inferenceApi, inferenceStatus, inputLabels, terminal, type InferenceSession, type InferenceReport } from '@/api/inference'
import { errorMessage } from '@/utils/errors'
import InferenceResultPanel from '@/components/results/InferenceResultPanel.vue'
const route = useRoute(); const router = useRouter()
const id = String(route.params.id)
const session = ref<InferenceSession | null>(null)
const report = ref<InferenceReport | null>(null)
const error = ref('')
const start = ref(''); const end = ref(''); const label = ref(''); const confidence = ref(0)
const applied = ref<Record<string, string | number | undefined>>({})
const filters = computed(() => ({ start: start.value === '' ? undefined : Number(start.value), end: end.value === '' ? undefined : Number(end.value), label: label.value || undefined, confidence: confidence.value || undefined }))
let timer: ReturnType<typeof setInterval> | undefined
let loading = false
async function load() { if (loading) return; loading = true; try { session.value = await inferenceApi.get(id); if (!report.value || terminal(session.value.status)) report.value = await inferenceApi.report(id, applied.value) } catch (e) { error.value = errorMessage(e, '读取任务失败') } finally { loading = false } }
async function apply() { error.value = ''; try { const value = await inferenceApi.report(id, filters.value); report.value = value; applied.value = { ...filters.value } } catch (e) { error.value = errorMessage(e, '报告读取失败') } }
async function stop() { try { session.value = await inferenceApi.stop(id) } catch (e) { error.value = errorMessage(e, '停止失败') } }
async function retry() { try { const result = await inferenceApi.retry(id); await router.push(`/inference/tasks/${result.id}`); location.reload() } catch (e) { error.value = errorMessage(e, '重试失败') } }
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden && session.value && !terminal(session.value.status)) void load() }, 2500) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <RouterLink class="text-button" to="/inference/tasks">← 推理任务</RouterLink>
  <p v-if="error" class="alert">{{ error }}</p>
  <template v-if="session"><header class="page-heading page-heading-actions"><div><h1>{{ session.name }}</h1><p>{{ inputLabels[session.kind] }} · {{ session.model.name }} · {{ inferenceStatus[session.status] }}</p></div><button v-if="!terminal(session.status)" class="secondary-button danger-button" @click="stop">停止推理</button><button v-else-if="session.kind !== 'browser' && (session.kind !== 'images' || session.failed || session.processed < session.config.asset_ids.length)" class="secondary-button" @click="retry">{{ session.kind === 'images' ? '重试失败及未处理图片' : '重新运行' }}</button></header>
    <div class="inference-statistics"><div><small>已处理 / 失败</small><strong>{{ session.processed }} / {{ session.failed }}</strong></div><div><small>累计轨迹（非去重人数）</small><strong>{{ session.config.tracking ? session.tracks : '未启用' }}</strong></div><div><small>重连 / 跳过源帧</small><strong>{{ session.reconnections }} / {{ session.skipped_frames }}</strong></div><div><small>生成产物占用</small><strong>{{ (session.bytes / 1048576).toFixed(1) }} MB</strong></div></div>
    <section class="section-block"><h2>结果预览</h2><InferenceResultPanel :session="session" /></section>
    <section class="section-block inference-report"><h2>指标与完整报告</h2><p class="muted-copy">没有真实标注时不计算准确率、召回率或 mAP。报告的统计范围与下方筛选保持一致。</p>
      <div class="inference-fields"><label v-if="session.kind !== 'images'">开始秒（含）<input v-model="start" type="number" min="0" /></label><label v-if="session.kind !== 'images'">结束秒（不含）<input v-model="end" type="number" min="0" /></label><label>检测类别<input v-model="label" placeholder="留空为全部" /></label><label>展示置信度下限<input v-model.number="confidence" type="number" min="0" max="1" step="0.01" /></label></div><button class="secondary-button" @click="apply">应用筛选并刷新报告</button>
      <template v-if="report"><p class="muted-copy">统计截止 {{ new Date(report.snapshot_at).toLocaleString() }} · {{ report.scope.mode === 'all' ? '全部结果' : '筛选结果' }}<template v-if="!terminal(session.status)"> · 运行中的阶段报告</template></p>
        <div class="inference-statistics"><div><small>范围内处理 / 失败</small><strong>{{ report.summary.processed }} / {{ report.summary.failed }}</strong></div><div><small>累计检测框</small><strong>{{ report.summary.detections }}</strong></div><div><small>平均模型推理（ms）</small><strong>{{ report.summary.mean_inference_ms?.toFixed(2) ?? '未采集' }}</strong></div><div><small>整个会话处理帧率</small><strong>{{ report.summary.session_processing_fps?.toFixed(2) ?? '未采集' }}</strong></div></div>
        <div class="inference-downloads"><a v-for="file in ['report.pdf','summary.csv','metrics.csv','report.json','results.json','classes.png','bundle.zip']" :key="file" class="secondary-button" :href="inferenceApi.download(id,file,applied)">{{ ({'report.pdf':'完整 PDF 报告','summary.csv':'CSV 汇总指标','metrics.csv':'CSV 结果明细','report.json':'报告 JSON','results.json':'完整结果 JSON','classes.png':'统计图表 PNG','bundle.zip':'完整产物 ZIP'} as Record<string,string>)[file] }}</a></div>
        <img class="inference-chart" :src="inferenceApi.download(id,'classes.png',applied)" alt="当前报告范围内的检测类别分布" />
        <ul class="muted-copy"><li v-for="note in report.notes" :key="note">{{ note }}</li></ul>
      </template>
      <div class="inference-downloads"><a v-for="(size,file) in session.artifacts" :key="file" class="secondary-button" :href="inferenceApi.file(id,String(file))" download>{{ file === 'result.mp4' ? '标注结果视频' : '采样录像' }} · {{ (size / 1048576).toFixed(1) }} MB（完整会话）</a></div>
      <p v-if="session.expires_at" class="muted-copy">生成画面和视频保留至 {{ new Date(session.expires_at).toLocaleString() }}；原始上传资源和结构化记录继续保留。</p>
      <details><summary>实际提交配置与模型来源</summary><pre class="annotation-example">{{ JSON.stringify({ model: session.model, config: session.config },null,2) }}</pre></details>
    </section>
  </template>
</template>
