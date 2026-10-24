<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { advancedApi, type MediaSource } from '@/api/advanced'
import { assetsApi } from '@/api/assets'
import { deploymentsApi, type Deployment, type DeploymentRoute } from '@/api/deployments'
import { inferenceApi, inputLabels, inferenceStatus, terminal, type InferenceConfig, type InferenceSession, type InputKind } from '@/api/inference'
import { systemApi, type GpuInfo } from '@/api/system'
import { tasksApi } from '@/api/tasks'
import { ApiError } from '@/api/http'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssetPages } from '@/composables/useAssetPages'
import { defaultParameters, parameterIssue, numericBounds, type ParameterValues } from '@/utils/parameters'
import { errorMessage } from '@/utils/errors'
import { taskTypeLabel } from '@/utils/presentation'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import InferenceResultPanel from '@/components/results/InferenceResultPanel.vue'
import type { ImageAsset } from '@/types/asset'

const route = useRoute()
const { availableAlgorithms, error: modelError } = useAlgorithms()
const kind = ref<InputKind>('images')
const category = ref('')
const modelId = ref('')
const search = ref('')
const family = ref('')
const modelFamily = (item: typeof availableAlgorithms.value[number]) => (item.lineage?.base_model === 'fasterrcnn_resnet50_fpn.pt' || item.key === 'faster-rcnn-resnet50') ? 'Faster R-CNN' : (item.pretrained?.base_model ?? item.lineage?.base_model ?? item.key).match(/^yolo\d+/i)?.[0]?.toUpperCase() ?? item.key
const families = computed(() => [...new Set(availableAlgorithms.value.filter(m => !category.value || m.task_type === category.value).map(modelFamily))])
const models = computed(() => availableAlgorithms.value.filter(m => (!category.value || m.task_type === category.value) && (!family.value || modelFamily(m) === family.value) && (!search.value || `${m.name} ${m.version}`.toLowerCase().includes(search.value.toLowerCase()))))
const model = computed(() => availableAlgorithms.value.find(m => m.id === modelId.value))
const parameters = ref<ParameterValues>({})
const quickParameters = computed(() => Object.entries(model.value?.parameters ?? {}).filter(([key, spec]) => typeof parameters.value[key] === 'number' && (spec.common || ['confidence','iou_threshold','nms_threshold','image_size','max_detections'].includes(key))).slice(0,4))
const editorIssue = ref('')
const error = ref('')
const busy = ref(false)
const name = ref('')
const library = useAssetPages(16)
const selected = ref<ImageAsset[]>([])
const sources = ref<MediaSource[]>([])
const sourceId = ref('')
const sourceName = ref('')
const sourceUri = ref('')
const sourcesForKind = computed(() => sources.value.filter(s => s.source_type === kind.value))
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const nodeId = ref('')
const gpuId = ref('')
const deployments = ref<Deployment[]>([])
const routes = ref<DeploymentRoute[]>([])
const deploymentId = ref('')
const placementMode = ref('auto')
const tracking = ref(false)
const interval = ref(1)
const maxFrames = ref(1000)
const duration = ref(60)
const reconnect = ref(3)
const saveVideo = ref(true)
const saveRecording = ref(false)
const storage = ref(512)
const retention = ref(7)
const active = ref<InferenceSession | null>(null)
const submittedConfig = ref('')
const webcam = ref<HTMLVideoElement | null>(null)
const cameras = ref<MediaDeviceInfo[]>([])
const cameraId = ref('')
let stream: MediaStream | null = null
let captureTimer: ReturnType<typeof setTimeout> | undefined
let refreshTimer: ReturnType<typeof setInterval> | undefined
let disposed = false
let pollBusy = false
const capturing = ref(false)
const config = computed<InferenceConfig>(() => ({ name: name.value, kind: kind.value, algorithm_version_id: modelId.value,
  parameters: parameters.value, asset_ids: kind.value === 'images' ? selected.value.map(a => a.id) : [], source_id: !['images', 'browser'].includes(kind.value) ? sourceId.value : null,
  node_id: placementMode.value === 'node' ? nodeId.value || null : null, gpu_device_ids: placementMode.value === 'node' && gpuId.value ? [gpuId.value] : [], deployment_id: placementMode.value === 'deployment' ? deploymentId.value || null : null,
  tracking: kind.value !== 'images' && model.value?.task_type === 'object_detection' && tracking.value,
  interval_seconds: interval.value, max_frames: maxFrames.value, duration_seconds: kind.value === 'images' || kind.value === 'video' ? 0 : Math.round(duration.value * 60), reconnect_attempts: reconnect.value,
  save_video: kind.value !== 'images' && saveVideo.value, save_recording: kind.value !== 'images' && saveRecording.value, storage_limit_mb: storage.value, retention_days: retention.value }))
const dirty = computed(() => Boolean(active.value && submittedConfig.value !== JSON.stringify(config.value)))
const issue = computed(() => editorIssue.value || parameterIssue(model.value?.parameters, parameters.value) || (!model.value ? '请选择模型版本' : kind.value === 'images' && !selected.value.length ? '请选择图片' : !['images','browser'].includes(kind.value) && !sourceId.value ? '请选择媒体源' : kind.value === 'browser' && !capturing.value ? '请先开启摄像头预览并授权' : placementMode.value === 'node' && !nodeId.value ? '请选择执行服务器' : placementMode.value === 'deployment' && !deploymentId.value ? '请选择运行中的部署实例' : ''))
watch(modelId, () => { parameters.value = defaultParameters(model.value?.parameters); deploymentId.value = ''; editorIssue.value = '' })
watch([category, family, search], () => { if (modelId.value && !models.value.some(m => m.id === modelId.value)) modelId.value = '' })
watch(nodeId, () => { gpuId.value = '' })
watch(kind, () => { sourceId.value = ''; if (capturing.value && (!active.value || terminal(active.value.status))) stopCamera() })
watch(availableAlgorithms, async () => {
  if (modelId.value) return
  modelId.value = typeof route.query.algorithm === 'string' ? route.query.algorithm : availableAlgorithms.value[0]?.id ?? ''
  await nextTick()
  if (typeof route.query.deployment === 'string') { deploymentId.value = route.query.deployment; placementMode.value = 'deployment' }
})
function toggle(asset: ImageAsset) { selected.value = selected.value.some(a => a.id === asset.id) ? selected.value.filter(a => a.id !== asset.id) : [...selected.value, asset].slice(0, 100) }
async function upload(files: File[]) {
  busy.value = true; error.value = ''
  try {
    if (kind.value === 'images') {
      const result = await assetsApi.upload(files)
      selected.value = [...selected.value, ...result.assets].filter((a, i, all) => all.findIndex(x => x.id === a.id) === i).slice(0, 100)
      await library.refresh()
    } else if (files[0]) {
      const item = await advancedApi.uploadVideo(files[0]); sources.value.unshift(item); sourceId.value = item.id
    }
  } catch (e) { error.value = errorMessage(e, '上传失败') } finally { busy.value = false }
}
async function addSource() {
  error.value = ''; busy.value = true
  try {
    if (kind.value !== 'rtsp' && kind.value !== 'camera') return
    const item = await advancedApi.createMediaSource({ name: sourceName.value, source_type: kind.value, uri: sourceUri.value })
    sources.value.unshift(item); sourceId.value = item.id; sourceUri.value = ''; sourceName.value = ''
  } catch (e) { error.value = errorMessage(e, '添加媒体源失败') } finally { busy.value = false }
}
async function startCamera() {
  error.value = ''; stopCamera()
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前访问环境不支持摄像头，请使用 localhost 或 HTTPS，并允许相机权限')
    stream = await navigator.mediaDevices.getUserMedia({ video: cameraId.value ? { deviceId: { exact: cameraId.value } } : { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false })
    cameras.value = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput')
    capturing.value = true; await nextTick()
    if (webcam.value) { webcam.value.srcObject = stream; await webcam.value.play() }
    stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => { stopCamera(); error.value = '摄像头已断开，服务端会标记采集中断' }))
  } catch (e) { error.value = errorMessage(e, '摄像头权限被拒绝或设备不可用') }
}
function stopCamera() { clearTimeout(captureTimer); stream?.getTracks().forEach(t => t.stop()); stream = null; capturing.value = false }
async function sendFrame() {
  if (disposed || !stream || !webcam.value || !active.value || terminal(active.value.status)) return
  try {
    const video = webcam.value; if (!video.videoWidth) throw new Error('摄像头尚未提供画面')
    const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight
    canvas.getContext('2d')!.drawImage(video, 0, 0)
    const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', .85))
    if (blob) await inferenceApi.uploadFrame(active.value.id, blob)
  } catch (e) { if (!(e instanceof ApiError && e.status === 429)) error.value = errorMessage(e, '画面发送失败') }
  if (!disposed && stream) captureTimer = setTimeout(() => void sendFrame(), Math.max(200, interval.value * 1000))
}
async function start() {
  if (issue.value) return
  busy.value = true; error.value = ''
  try { active.value = await inferenceApi.create(config.value); submittedConfig.value = JSON.stringify(config.value); if (kind.value === 'browser') void sendFrame() }
  catch (e) { error.value = errorMessage(e, '创建推理失败') } finally { busy.value = false }
}
async function stop() { if (!active.value) return; try { active.value = await inferenceApi.stop(active.value.id); stopCamera() } catch (e) { error.value = errorMessage(e, '停止失败') } }
onMounted(async () => {
  try {
    const [media, resources, deployed, services] = await Promise.all([advancedApi.mediaSources(), systemApi.executionResources(), deploymentsApi.list().catch(() => []), deploymentsApi.listRoutes().catch(() => [])])
    sources.value = media; nodes.value = resources.nodes; gpus.value = resources.gpus; deployments.value = deployed; routes.value = services
    if (typeof route.query.service === 'string') { const selectedRoute = routes.value.find(s => s.key === route.query.service); if (selectedRoute?.active_deployment_id) { await nextTick(); deploymentId.value = selectedRoute.active_deployment_id; placementMode.value = 'deployment' } }
    const ids = [route.query.asset, ...(typeof route.query.assets === 'string' ? route.query.assets.split(',') : [])].filter((s): s is string => typeof s === 'string')
    if (ids.length) selected.value = await assetsApi.byIds(ids)
    if (typeof route.query.from_task === 'string') {
      const task = await tasksApi.get(route.query.from_task); modelId.value = task.algorithm_version_id; await nextTick(); parameters.value = task.parameters as ParameterValues
      if (task.asset_id) selected.value = await assetsApi.byIds([task.asset_id])
      if (task.deployment_id) { deploymentId.value = task.deployment_id; placementMode.value = 'deployment' }
    }
  } catch (e) { error.value = errorMessage(e, '加载配置失败') }
  refreshTimer = setInterval(async () => { if (!active.value || terminal(active.value.status) || pollBusy) return; pollBusy = true; try { active.value = await inferenceApi.get(active.value.id); if (terminal(active.value.status)) stopCamera() } catch { /* next refresh */ } finally { pollBusy = false } }, 2000)
})
onUnmounted(() => { disposed = true; stopCamera(); clearInterval(refreshTimer) })
</script>

<template>
  <div class="inference-center">
    <header class="page-heading page-heading-actions"><div><h1>推理中心</h1><p>选择模型和输入，运行推理，查看结果与报告。</p></div><RouterLink class="secondary-button" to="/inference/tasks">推理任务</RouterLink></header>
    <p v-if="error || modelError" class="alert" role="alert">{{ error || modelError }}</p>
    <div class="inference-modes" role="group" aria-label="输入模式"><button v-for="mode in (['images','video','browser','camera','rtsp'] as InputKind[])" :key="mode" class="secondary-button" :class="{ selected: kind === mode }" :disabled="Boolean(active && !terminal(active.status))" @click="kind = mode">{{ inputLabels[mode] }}</button></div>
    <div class="inference-setup">
      <section class="section-block"><h2>1. 选择模型</h2>
        <div class="inference-fields"><label>任务类型<select v-model="category"><option value="">全部类型</option><option v-for="(label, key) in taskTypeLabel" :key="key" :value="key">{{ label }}</option></select></label><label>搜索模型<input v-model="search" type="search" placeholder="模型名称或版本" /></label></div>
        <label>算法 / 模型系列<select v-model="family" aria-label="推理算法系列"><option value="">全部系列</option><option v-for="value in families" :key="value" :value="value">{{ value }}</option></select></label>
        <label>模型版本<select v-model="modelId" aria-label="推理模型"><option value="" disabled>请选择已发布的可用模型</option><option v-for="item in models" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }} · {{ item.device.toUpperCase() }}</option></select></label>
        <p v-if="model" class="muted-copy">{{ model.pretrained ? '官方预训练模型 · COCO 80 类' : model.lineage ? '平台训练后发布的模型' : '已导入 / 预置模型' }} · {{ taskTypeLabel[model.task_type] }}<template v-if="model.lineage"> · {{ model.lineage.base_model }}</template></p>
        <RouterLink class="text-button" to="/algorithms">管理模型与算法</RouterLink>
        <div v-if="quickParameters.length" class="inference-fields"><label v-for="[key, spec] in quickParameters" :key="key">{{ spec.title || key }}<input v-model.number="parameters[key]" type="number" :min="numericBounds(spec).min" :max="numericBounds(spec).max" :step="numericBounds(spec).step" /></label></div>
        <details class="inference-parameters"><summary>推理参数 <span class="muted-copy">{{ Object.keys(parameters).length }} 项</span></summary><ParameterForm v-if="model" v-model="parameters" :parameters="model.parameters" @validity="editorIssue = $event" /></details>
        <details><summary>执行位置 <span class="muted-copy">{{ placementMode === 'auto' ? '自动调度' : '手动指定' }}</span></summary>
          <label>运行方式<select v-model="placementMode"><option value="auto">自动调度</option><option value="node">指定服务器</option><option value="deployment">使用已部署实例</option></select></label>
          <template v-if="placementMode === 'node'"><label>服务器<select v-model="nodeId"><option value="">请选择</option><option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.name }}</option></select></label><label v-if="model?.device !== 'cpu'">GPU<select v-model="gpuId"><option value="">自动选择</option><option v-for="gpu in gpus.filter(g => g.node_id === nodeId)" :key="gpu.index" :value="String(gpu.index)">GPU {{ gpu.index }} · {{ gpu.name }}</option></select></label></template>
          <label v-if="placementMode === 'deployment'">运行实例<select v-model="deploymentId"><option value="">请选择</option><option v-for="d in deployments.filter(d => d.algorithm_version_id === modelId && d.status === 'running')" :key="d.id" :value="d.id">{{ d.node_id }} · {{ d.id.slice(0,8) }}</option></select></label>
          <p class="muted-copy">采集在媒体服务器进行；推理可自动调度到计算节点。模型未启动时需要等待加载。</p>
        </details>
      </section>
      <section class="section-block"><h2>2. 输入与处理方式</h2>
        <template v-if="kind === 'images'">
          <UploadDropzone :disabled="busy" compact title="上传图片" hint="可多选；本次最多 100 张" @files="upload" />
          <details><summary>从图片库选择（已选 {{ selected.length }} 张）</summary><input v-model="library.query.value" type="search" aria-label="搜索输入图片" placeholder="搜索图片" /><div class="inference-image-picker"><button v-for="asset in library.items.value" :key="asset.id" :class="{ selected: selected.some(a => a.id === asset.id) }" @click="toggle(asset)"><img :src="assetsApi.thumbnailUrl(asset.id,160)" :alt="asset.original_name" /><span>{{ asset.original_name }}</span></button></div><button v-if="library.hasMore.value" class="text-button" @click="library.loadMore">加载更多</button></details>
          <div class="inference-selection"><span>已选 {{ selected.length }} 张</span><button v-if="selected.length" class="text-button" @click="selected = []">清空</button></div>
          <div class="filter-chips"><button v-for="asset in selected" :key="asset.id" :title="asset.original_name" @click="toggle(asset)">{{ asset.original_name }} ×</button></div>
        </template>
        <UploadDropzone v-if="kind === 'video'" accept=".mp4,.avi,.mov,.mkv,.webm" :multiple="false" :disabled="busy" compact title="上传视频" hint="MP4、AVI、MOV、MKV、WebM" @files="upload" />
        <template v-if="!['images','browser'].includes(kind)"><label>媒体源<select v-model="sourceId"><option value="">请选择</option><option v-for="source in sourcesForKind" :key="source.id" :value="source.id">{{ source.name }}</option></select></label>
          <details v-if="kind === 'camera' || kind === 'rtsp'"><summary>添加{{ inputLabels[kind] }}</summary><label>名称<input v-model="sourceName" maxlength="128" /></label><label>{{ kind === 'camera' ? '设备地址（媒体服务器）' : 'RTSP 地址' }}<input v-model="sourceUri" :type="kind === 'rtsp' ? 'password' : 'text'" :placeholder="kind === 'camera' ? 'camera:0' : 'rtsp://…'" autocomplete="off" /></label><button class="secondary-button" :disabled="busy || !sourceName || !sourceUri" @click="addSource">添加来源</button><p v-if="kind === 'camera'" class="muted-copy">需由部署管理员将摄像头设备映射给媒体服务。</p></details>
        </template>
        <template v-if="kind === 'browser'"><label v-if="cameras.length">当前电脑摄像头<select v-model="cameraId" :disabled="Boolean(active && !terminal(active.status))"><option value="">默认摄像头</option><option v-for="device in cameras" :key="device.deviceId" :value="device.deviceId">{{ device.label }}</option></select></label><button class="secondary-button" :disabled="Boolean(active && !terminal(active.status))" @click="startCamera">{{ capturing ? '重新开启预览' : '开启摄像头预览' }}</button><video v-show="capturing" ref="webcam" class="inference-camera" autoplay muted playsinline /><p class="muted-copy">此来源依赖当前页面。关闭页面或停止提供画面 15 秒后，会话标记为采集中断。</p></template>
        <template v-if="kind !== 'images'">
          <div class="inference-fields"><label>{{ kind === 'video' ? '抽帧间隔（秒，0 为全帧）' : '采样间隔（秒）' }}<input v-model.number="interval" type="number" min="0" max="60" step="0.1" /></label><label>最多处理帧数<input v-model.number="maxFrames" type="number" min="1" max="10000" /></label><label v-if="kind !== 'video'">定时停止（分钟，0 为手动）<input v-model.number="duration" type="number" min="0" max="480" /></label><label v-if="kind === 'camera' || kind === 'rtsp'">最多重连次数<input v-model.number="reconnect" type="number" min="0" max="5" /></label></div>
          <label v-if="model?.task_type === 'object_detection'" class="check-control"><input v-model="tracking" type="checkbox" />开启目标跟踪（同路 IoU 轨迹关联）</label><p v-if="tracking" class="muted-copy">轨迹数不等于去重人数；抽帧间隔大于 2 秒时无法连续关联。断流重连后建立新轨迹片段。</p>
          <label class="check-control"><input v-model="saveVideo" type="checkbox" />生成标注结果视频</label><label class="check-control"><input v-model="saveRecording" type="checkbox" />保存已采集的原始画面录像</label><small class="muted-copy">视频保留已处理画面的时间间隔，不保留音轨。实时输入超出处理能力时仅处理最新画面。</small>
        </template>
        <details><summary>产物保存与任务名称</summary><label>任务名称<input v-model="name" maxlength="128" /></label><div class="inference-fields"><label>产物上限（MB）<input v-model.number="storage" type="number" min="32" max="2048" /></label><label>产物保留（天）<input v-model.number="retention" type="number" min="1" max="90" /></label></div><small class="muted-copy">到期清理本次生成的预览、样例和视频；已上传资源及结构化结果保留在任务记录。</small></details>
        <div class="inference-actions"><div><strong>{{ model?.name || '尚未选择模型' }}</strong><p class="muted-copy">{{ issue || `${inputLabels[kind]} · ${kind === 'images' ? selected.length + ' 张' : '最多 ' + maxFrames + ' 帧'}` }}</p></div><button v-if="active && !terminal(active.status)" class="secondary-button danger-button" @click="stop">停止本次推理</button><button v-else class="primary-button" :disabled="busy || Boolean(issue)" @click="start">{{ busy ? '正在提交…' : '开始推理' }}</button></div>
      </section>
    </div>

    <section v-if="active" class="section-block inference-live-result"><header class="section-heading"><div><h2>本次推理 · {{ inferenceStatus[active.status] }}</h2><p>已处理 {{ active.processed }} · 失败 {{ active.failed }}<template v-if="active.config.tracking"> · 累计轨迹 {{ active.tracks }}</template></p></div><RouterLink class="secondary-button" :to="`/inference/tasks/${active.id}`">任务与报告</RouterLink></header><p v-if="dirty" class="build-notice">配置已修改，需要重新推理。下方仍是已提交配置的结果。</p><InferenceResultPanel :session="active" /></section>
    <section v-else class="empty-state"><strong>结果将在这里显示</strong><p>选择输入并开始推理；历史结果请到推理任务查看。</p></section>
  </div>
</template>
