<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { advancedApi, type MediaRun, type MediaSource, type RuntimeNode, type ScalingPolicy, type Workflow, type WorkflowRun } from '@/api/advanced'
import { systemApi, type GpuInfo } from '@/api/system'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import AssetPicker from '@/components/assets/AssetPicker.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useAssets } from '@/composables/useAssets'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { defaultParameters, parameterIssue, type ParameterValues } from '@/utils/parameters'
import { formatDateTime, nodeLabel, runStatusLabel } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

type Tab = 'media' | 'workflow' | 'nodes' | 'scaling' | 'deploy'
const tabs: Array<{ id: Tab; label: string }> = [
  { id: 'media', label: '媒体推理' }, { id: 'workflow', label: '工作流' }, { id: 'nodes', label: '运行节点' },
  { id: 'scaling', label: '扩缩容与灰度' }, { id: 'deploy', label: '部署与指标' },
]
const sourceTypeLabel: Record<MediaSource['source_type'], string> = { rtsp: 'RTSP 视频流', camera: '摄像头', video: '视频文件' }
const sourceTypeHint: Record<MediaSource['source_type'], string> = {
  rtsp: '例如 rtsp://192.168.1.20:554/stream1，需要 Media Worker 能访问该地址',
  camera: '格式为 camera:0，对应服务器上的 /dev/video0',
  video: '视频文件建议直接用上方的“上传视频”，手工填写需位于 file:///data/ 目录',
}

const { algorithms, availableAlgorithms } = useAlgorithms()
const { assets } = useAssets()
const activeTab = ref<Tab>('media')
const busy = ref(false)
const media = ref<MediaSource[]>([])
const mediaRuns = ref<MediaRun[]>([])
const workflows = ref<Workflow[]>([])
const workflowRuns = ref<WorkflowRun[]>([])
const nodes = ref<RuntimeNode[]>([])
const gpus = ref<GpuInfo[]>([])
const policies = ref<ScalingPolicy[]>([])
const metrics = ref<Record<string, unknown>>({})
const error = ref('')
const videoUploading = ref(false)

// 媒体推理
const sourceName = ref('')
const sourceType = ref<MediaSource['source_type']>('rtsp')
const sourceUri = ref('')
const mediaSourceId = ref('')
const mediaAlgorithmId = ref('')
const mediaParameters = ref<ParameterValues>({})
const mediaInterval = ref(1)
const mediaMaxFrames = ref(100)
// 工作流
const workflowName = ref('')
const workflowMode = ref<'sequential' | 'parallel'>('sequential')
const workflowSteps = ref<string[]>([])
const workflowId = ref('')
const workflowAssetIds = ref<string[]>([])
// 运行节点
const node = ref<RuntimeNode>({ id: '', name: '', manager_url: '', enabled: true })
// 扩缩容与灰度
const scalingAlgorithmId = ref('')
const minReplicas = ref(0)
const maxReplicas = ref(2)
const targetConcurrency = ref(1)
const idleSeconds = ref(1800)
const trafficFamily = ref('')
const trafficWeights = ref<Record<string, number>>({})
// 部署导出
const exportAlgorithmId = ref('')
const exportBackend = ref<'docker' | 'bentoml' | 'kserve'>('docker')
const exportFiles = ref<Record<string, string>>({})
const exportFile = ref('')

const mediaAlgorithm = computed(() => availableAlgorithms.value.find((item) => item.id === mediaAlgorithmId.value))
const algorithmFamilies = computed(() => [...new Set(algorithms.value.map((item) => item.key))])
const trafficVersions = computed(() => algorithms.value.filter((item) => item.key === trafficFamily.value && ['available', 'disabled'].includes(item.status)))
const trafficTotal = computed(() => trafficVersions.value.reduce((sum, item) => sum + Number(trafficWeights.value[item.id] ?? item.traffic_weight), 0))
const nodeIdValid = computed(() => /^[a-z0-9][a-z0-9-]{1,63}$/.test(node.value.id))
const nodeUrlValid = computed(() => /^https?:\/\/.+/.test(node.value.manager_url))
const tasksByStatus = computed(() => Object.entries((metrics.value.tasks_by_status ?? {}) as Record<string, number>))
const hasActiveRuns = computed(() => mediaRuns.value.some((run) => ['queued', 'extracting', 'running'].includes(run.status)) || workflowRuns.value.some((run) => ['queued', 'running'].includes(run.status)))

watch(mediaAlgorithm, (algorithm) => { mediaParameters.value = defaultParameters(algorithm?.parameters) })
watch(exportFiles, (files) => { exportFile.value = Object.keys(files)[0] ?? '' })

function algorithmName(id: string) {
  const algorithm = algorithms.value.find((item) => item.id === id)
  return algorithm ? `${algorithm.name} v${algorithm.version}` : '已删除的算法'
}
function mediaSourceName(id: string) { return media.value.find((item) => item.id === id)?.name ?? '已删除的媒体源' }
function workflowLabel(id: string) { return workflows.value.find((item) => item.id === id)?.name ?? '已删除的工作流' }
function statusClass(status: string) {
  return ['completed', 'succeeded'].includes(status) ? 'status-healthy' : ['failed', 'interrupted'].includes(status) ? 'status-failed' : status === 'cancelled' ? '' : 'status-starting'
}

async function refresh() {
  try {
    ;[media.value, mediaRuns.value, workflows.value, workflowRuns.value, nodes.value, policies.value, metrics.value] = await Promise.all([
      advancedApi.mediaSources(), advancedApi.mediaRuns(), advancedApi.workflows(), advancedApi.workflowRuns(), advancedApi.runtimeNodes(), advancedApi.scalingPolicies(), advancedApi.metrics(),
    ])
    error.value = ''
  } catch (reason) { error.value = errorMessage(reason, '高级功能加载失败') }
}
async function refreshResources() {
  try { gpus.value = (await systemApi.executionResources()).gpus } catch { gpus.value = [] }
}

/** 统一执行写操作：成功给提示并刷新，失败把原因显示出来 */
async function act(action: () => Promise<unknown>, success: string, fallback: string) {
  if (busy.value) return false
  busy.value = true
  try {
    await action()
    toast.success(success)
    await refresh()
    return true
  } catch (reason) {
    toast.error(errorMessage(reason, fallback))
    return false
  } finally { busy.value = false }
}

async function addSource() {
  const ok = await act(() => advancedApi.createMediaSource({ name: sourceName.value.trim(), source_type: sourceType.value, uri: sourceUri.value.trim() }), `已注册媒体源 ${sourceName.value}`, '注册媒体源失败')
  if (ok) { sourceName.value = ''; sourceUri.value = '' }
}
async function uploadVideo(files: File[]) {
  const file = files[0]
  if (!file) return
  videoUploading.value = true
  try { await act(() => advancedApi.uploadVideo(file), `已上传 ${file.name}`, '上传视频失败') } finally { videoUploading.value = false }
}
function removeSource(source: MediaSource) {
  if (!window.confirm(`删除媒体源“${source.name}”？已产生的抽帧任务不受影响。`)) return
  void act(() => advancedApi.deleteMediaSource(source.id), '已删除媒体源', '删除媒体源失败')
}
function startMediaRun() {
  const issue = parameterIssue(mediaAlgorithm.value?.parameters, mediaParameters.value)
  if (issue) { toast.error(issue); return }
  void act(() => advancedApi.startMediaRun(mediaSourceId.value, {
    algorithm_version_id: mediaAlgorithmId.value, parameters: mediaParameters.value,
    interval_seconds: mediaInterval.value, max_frames: mediaMaxFrames.value,
  }), '媒体推理已开始，运行记录会自动刷新', '发起媒体推理失败')
}
function toggleStep(id: string) {
  workflowSteps.value = workflowSteps.value.includes(id) ? workflowSteps.value.filter((item) => item !== id) : [...workflowSteps.value, id]
}
async function addWorkflow() {
  const steps = workflowSteps.value.map((id, index) => ({
    id: `step_${index + 1}`, algorithm_version_id: id, parameters: {},
    depends_on: workflowMode.value === 'sequential' && index ? [`step_${index}`] : [],
  }))
  const ok = await act(() => advancedApi.createWorkflow({ name: workflowName.value.trim(), mode: workflowMode.value, nodes: steps }), `已创建工作流 ${workflowName.value}`, '创建工作流失败')
  if (ok) { workflowName.value = ''; workflowSteps.value = [] }
}
function startWorkflow() {
  void act(() => advancedApi.startWorkflow(workflowId.value, workflowAssetIds.value[0]!), '工作流已开始运行', '运行工作流失败')
}
function cancelWorkflow(run: WorkflowRun) {
  void act(() => advancedApi.cancelWorkflowRun(run.id), '已取消工作流运行', '取消失败')
}
function editNode(item: RuntimeNode) { node.value = { ...item } }
async function saveNode() {
  const existed = nodes.value.some((item) => item.id === node.value.id)
  const ok = await act(() => advancedApi.registerNode({ ...node.value, name: node.value.name.trim(), manager_url: node.value.manager_url.trim() }), existed ? `已更新节点 ${node.value.name}` : `已注册节点 ${node.value.name}`, '保存节点失败')
  if (ok) { node.value = { id: '', name: '', manager_url: '', enabled: true }; void refreshResources() }
}
function saveScaling() {
  void act(async () => {
    await advancedApi.setScalingPolicy(scalingAlgorithmId.value, {
      min_replicas: minReplicas.value, max_replicas: maxReplicas.value,
      target_concurrency: targetConcurrency.value, idle_seconds: idleSeconds.value,
    })
    await advancedApi.reconcileScaling()
  }, '扩缩容策略已保存并完成一次协调', '保存扩缩容策略失败')
}
function editPolicy(policy: ScalingPolicy) {
  scalingAlgorithmId.value = policy.algorithm_version_id
  minReplicas.value = policy.min_replicas; maxReplicas.value = policy.max_replicas
  targetConcurrency.value = policy.target_concurrency; idleSeconds.value = policy.idle_seconds
}
function saveTraffic() {
  void act(() => advancedApi.setTraffic(Object.fromEntries(trafficVersions.value.map((item) => [item.id, Number(trafficWeights.value[item.id] ?? item.traffic_weight)]))), '灰度权重已发布', '发布权重失败')
}
async function exportDeployment() {
  try {
    exportFiles.value = (await advancedApi.deploymentManifest(exportAlgorithmId.value, exportBackend.value)).files
  } catch (reason) { toast.error(errorMessage(reason, '生成部署清单失败')) }
}
async function copyExport() {
  try { await navigator.clipboard.writeText(exportFiles.value[exportFile.value] ?? ''); toast.success(`已复制 ${exportFile.value}`) } catch { toast.error('浏览器不允许复制，请手动选择文本') }
}

onMounted(() => { void refresh(); void refreshResources() })
const polling = window.setInterval(() => { if (hasActiveRuns.value && !document.hidden) void refresh() }, 4000)
onUnmounted(() => window.clearInterval(polling))
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>高级编排</h1><p>视频与摄像头推理、多算法工作流、远程 GPU 节点、扩缩容和部署导出。</p></div>
    <button class="secondary-button" @click="refresh">刷新</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <nav class="tab-bar" role="tablist" aria-label="高级功能">
    <button v-for="tab in tabs" :key="tab.id" role="tab" type="button" :aria-selected="activeTab === tab.id" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">{{ tab.label }}</button>
  </nav>

  <div v-show="activeTab === 'media'" class="advanced-grid" role="tabpanel">
    <section class="section-block">
      <header class="section-heading"><div><h2>媒体源</h2><span class="count-badge">{{ media.length }}</span></div></header>
      <UploadDropzone accept="video/mp4,video/webm,video/quicktime,video/x-msvideo,.mkv" :multiple="false" :disabled="videoUploading" compact :title="videoUploading ? '正在上传视频…' : '上传视频文件'" hint="MP4、WebM、MOV、AVI、MKV，上传后自动登记为媒体源" @files="uploadVideo" />
      <form class="stacked-form" @submit.prevent="addSource">
        <label>名称<input v-model="sourceName" required maxlength="128" placeholder="例如：一号门摄像头" /></label>
        <label>类型<select v-model="sourceType"><option v-for="(label, value) in sourceTypeLabel" :key="value" :value="value">{{ label }}</option></select></label>
        <label>地址<input v-model="sourceUri" required maxlength="2048" :placeholder="sourceType === 'camera' ? 'camera:0' : sourceType === 'rtsp' ? 'rtsp://' : 'file:///data/videos/…'" /><small>{{ sourceTypeHint[sourceType] }}</small></label>
        <button class="primary-button" :disabled="busy || !sourceName.trim() || !sourceUri.trim()">注册媒体源</button>
      </form>
      <ul class="plain-list">
        <li v-for="item in media" :key="item.id"><span><strong>{{ item.name }}</strong><small class="mono">{{ sourceTypeLabel[item.source_type] }} · {{ item.uri }}</small></span><button class="secondary-button danger-button compact" :disabled="busy" @click="removeSource(item)">删除</button></li>
        <li v-if="!media.length" class="muted-copy">还没有媒体源。</li>
      </ul>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>发起媒体推理</h2></div></header>
      <form class="stacked-form" @submit.prevent="startMediaRun">
        <label>媒体源<select v-model="mediaSourceId" required><option value="" disabled>选择媒体源</option><option v-for="item in media" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <label>算法<select v-model="mediaAlgorithmId" required><option value="" disabled>选择算法</option><option v-for="item in availableAlgorithms" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }}</option></select></label>
        <div class="form-row">
          <label>抽帧间隔（秒）<input v-model.number="mediaInterval" type="number" min="0.1" max="60" step="0.1" /></label>
          <label>最多处理帧数<input v-model.number="mediaMaxFrames" type="number" min="1" max="10000" /></label>
        </div>
        <details v-if="mediaAlgorithm && Object.keys(mediaAlgorithm.parameters).length" class="inline-details"><summary>算法参数</summary><ParameterForm v-model="mediaParameters" :parameters="mediaAlgorithm.parameters" /></details>
        <button class="primary-button" :disabled="busy || !mediaSourceId || !mediaAlgorithmId">开始媒体推理</button>
      </form>
    </section>
    <section class="section-block wide">
      <header class="section-heading"><div><h2>媒体推理记录</h2><span class="count-badge">{{ mediaRuns.length }}</span></div></header>
      <div v-if="!mediaRuns.length" class="empty-state">还没有媒体推理记录。</div>
      <ul v-else class="run-list">
        <li v-for="run in mediaRuns" :key="run.id">
          <span><strong>{{ mediaSourceName(run.source_id) }}</strong><small>{{ algorithmName(run.algorithm_version_id) }} · {{ formatDateTime(run.created_at) }}</small></span>
          <span :class="['runtime-status', statusClass(run.status)]">{{ runStatusLabel[run.status] ?? run.status }}</span>
          <span class="muted-copy">{{ run.frame_task_ids.length }} 帧</span>
          <span class="run-links"><RouterLink v-for="(taskId, index) in run.frame_task_ids.slice(0, 3)" :key="taskId" class="text-button" :to="`/tasks/${taskId}`">第 {{ index + 1 }} 帧</RouterLink><RouterLink v-if="run.frame_task_ids.length > 3" class="text-button" :to="`/tasks/${run.frame_task_ids[run.frame_task_ids.length - 1]}`">最后一帧</RouterLink></span>
          <small v-if="run.error_message" class="run-error">{{ run.error_message }}</small>
        </li>
      </ul>
    </section>
  </div>

  <div v-show="activeTab === 'workflow'" class="advanced-grid" role="tabpanel">
    <section class="section-block">
      <header class="section-heading"><div><h2>创建工作流</h2></div></header>
      <form class="stacked-form" @submit.prevent="addWorkflow">
        <label>名称<input v-model="workflowName" required maxlength="128" placeholder="例如：先检测再分类" /></label>
        <label>执行方式<select v-model="workflowMode"><option value="sequential">串行：按步骤顺序依次执行</option><option value="parallel">并行：所有步骤同时执行</option></select></label>
        <fieldset class="step-picker"><legend>步骤（按点击顺序排列）</legend>
          <button v-for="item in availableAlgorithms" :key="item.id" type="button" :class="{ selected: workflowSteps.includes(item.id) }" :aria-pressed="workflowSteps.includes(item.id)" @click="toggleStep(item.id)">
            <span v-if="workflowSteps.includes(item.id)" class="step-index">{{ workflowSteps.indexOf(item.id) + 1 }}</span>{{ item.name }}<small>v{{ item.version }}</small>
          </button>
          <p v-if="!availableAlgorithms.length" class="muted-copy">没有可用算法。</p>
        </fieldset>
        <button class="primary-button" :disabled="busy || !workflowName.trim() || !workflowSteps.length">创建工作流</button>
      </form>
      <ul class="plain-list">
        <li v-for="item in workflows" :key="item.id"><span><strong>{{ item.name }}</strong><small>{{ item.mode === 'sequential' ? '串行' : '并行' }} · {{ item.nodes.map((step) => algorithmName(step.algorithm_version_id)).join(' → ') }}</small></span></li>
      </ul>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>运行工作流</h2></div></header>
      <form class="stacked-form" @submit.prevent="startWorkflow">
        <label>工作流<select v-model="workflowId" required><option value="" disabled>选择工作流</option><option v-for="item in workflows" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <p class="picker-label">输入图片</p>
        <AssetPicker v-model="workflowAssetIds" :assets="assets" :page-size="12" />
        <button class="primary-button" :disabled="busy || !workflowId || !workflowAssetIds.length">运行工作流</button>
      </form>
    </section>
    <section class="section-block wide">
      <header class="section-heading"><div><h2>工作流运行记录</h2><span class="count-badge">{{ workflowRuns.length }}</span></div></header>
      <div v-if="!workflowRuns.length" class="empty-state">还没有工作流运行记录。</div>
      <ul v-else class="run-list">
        <li v-for="run in workflowRuns" :key="run.id">
          <span><strong>{{ workflowLabel(run.workflow_id) }}</strong><small>{{ formatDateTime(run.created_at) }}</small></span>
          <span :class="['runtime-status', statusClass(run.status)]">{{ runStatusLabel[run.status] ?? run.status }}</span>
          <span class="run-links"><RouterLink v-for="(taskId, step) in run.node_tasks" :key="step" class="text-button" :to="`/tasks/${taskId}`">{{ step }}</RouterLink></span>
          <button v-if="['queued', 'running'].includes(run.status)" class="secondary-button danger-button compact" :disabled="busy" @click="cancelWorkflow(run)">取消</button>
          <small v-if="run.error_message" class="run-error">{{ run.error_message }}</small>
        </li>
      </ul>
    </section>
  </div>

  <div v-show="activeTab === 'nodes'" class="advanced-grid" role="tabpanel">
    <section class="section-block">
      <header class="section-heading"><div><h2>运行节点</h2><span class="count-badge">{{ nodes.length }}</span></div><button class="text-button" @click="refreshResources">刷新 GPU 状态</button></header>
      <ul class="plain-list">
        <li v-for="item in nodes" :key="item.id">
          <span>
            <strong>{{ nodeLabel(item.id, nodes) }}<span v-if="!item.enabled" class="runtime-status">已停用</span></strong>
            <small class="mono">{{ item.id }} · {{ item.manager_url }}</small>
            <small v-for="gpu in gpus.filter((card) => card.node_id === item.id)" :key="gpu.index">GPU {{ gpu.index }} · {{ gpu.name }} · 已用 {{ (gpu.memory_used_mb / 1024).toFixed(1) }} / {{ (gpu.memory_total_mb / 1024).toFixed(1) }} GB</small>
            <small v-if="item.enabled && !gpus.some((card) => card.node_id === item.id)" class="muted-copy">未检测到 GPU，或节点暂时无法连接</small>
          </span>
          <button class="secondary-button compact" @click="editNode(item)">编辑</button>
        </li>
      </ul>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>{{ nodes.some((item) => item.id === node.id) ? '编辑节点' : '注册节点' }}</h2></div></header>
      <form class="stacked-form" @submit.prevent="saveNode">
        <label>节点 ID<input v-model="node.id" required placeholder="例如 dell-gpu" :aria-invalid="Boolean(node.id) && !nodeIdValid" /><small>小写字母、数字和短横线；ID 已存在时会更新该节点</small></label>
        <label>显示名称<input v-model="node.name" required maxlength="128" placeholder="例如 Dell RTX 5070 Ti" /></label>
        <label>Manager 地址<input v-model="node.manager_url" required placeholder="http://192.168.8.135:8010/api/v1" :aria-invalid="Boolean(node.manager_url) && !nodeUrlValid" /><small>节点设置了访问令牌时，主平台需要配置相同的 CV_PLATFORM_MANAGER_API_TOKEN</small></label>
        <label class="check-control"><input v-model="node.enabled" type="checkbox" role="switch" /> 参与调度</label>
        <button class="primary-button" :disabled="busy || !nodeIdValid || !node.name.trim() || !nodeUrlValid">保存节点</button>
      </form>
    </section>
  </div>

  <div v-show="activeTab === 'scaling'" class="advanced-grid" role="tabpanel">
    <section class="section-block">
      <header class="section-heading"><div><h2>自动扩缩容</h2></div></header>
      <form class="stacked-form" @submit.prevent="saveScaling">
        <label>算法版本<select v-model="scalingAlgorithmId" required><option value="" disabled>选择算法版本</option><option v-for="item in availableAlgorithms" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }}</option></select></label>
        <div class="form-row">
          <label>最少副本<input v-model.number="minReplicas" type="number" min="0" max="100" /></label>
          <label>最多副本<input v-model.number="maxReplicas" type="number" min="1" max="100" /></label>
        </div>
        <div class="form-row">
          <label>单副本并发<input v-model.number="targetConcurrency" type="number" min="1" max="1000" /></label>
          <label>空闲回收（秒）<input v-model.number="idleSeconds" type="number" min="30" max="86400" /></label>
        </div>
        <button class="primary-button" :disabled="busy || !scalingAlgorithmId || minReplicas > maxReplicas">保存并立即协调</button>
      </form>
      <ul class="plain-list">
        <li v-for="item in policies" :key="item.algorithm_version_id"><span><strong>{{ algorithmName(item.algorithm_version_id) }}</strong><small>{{ item.min_replicas }}–{{ item.max_replicas }} 个副本 · 单副本并发 {{ item.target_concurrency }} · 空闲 {{ item.idle_seconds }} 秒回收</small></span><button class="secondary-button compact" @click="editPolicy(item)">编辑</button></li>
      </ul>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>灰度流量</h2></div></header>
      <form class="stacked-form" @submit.prevent="saveTraffic">
        <label>算法<select v-model="trafficFamily"><option value="">选择算法</option><option v-for="key in algorithmFamilies" :key="key" :value="key">{{ algorithms.find((item) => item.key === key)?.name ?? key }}（{{ key }}）</option></select></label>
        <label v-for="item in trafficVersions" :key="item.id">v{{ item.version }} 流量权重（%）<input v-model.number="trafficWeights[item.id]" type="number" min="0" max="100" :placeholder="String(item.traffic_weight)" /></label>
        <p v-if="trafficFamily" :class="trafficTotal === 100 ? 'muted-copy' : 'alert'">当前合计 {{ trafficTotal }}%{{ trafficTotal === 100 ? '' : '，需要等于 100%' }}</p>
        <button class="primary-button" :disabled="busy || !trafficFamily || trafficTotal !== 100">发布权重</button>
      </form>
    </section>
  </div>

  <div v-show="activeTab === 'deploy'" class="advanced-grid" role="tabpanel">
    <section class="section-block">
      <header class="section-heading"><div><h2>运行指标</h2></div></header>
      <div class="metric-grid">
        <div><span>平均推理耗时</span><strong>{{ Number(metrics.average_inference_ms ?? 0).toFixed(1) }} ms</strong></div>
        <div><span>已完成样本</span><strong>{{ metrics.completed_samples ?? 0 }}</strong></div>
        <div><span>运行实例</span><strong>{{ metrics.runtime_instances ?? 0 }}</strong></div>
        <div><span>扩缩容策略</span><strong>{{ metrics.autoscaling_policies ?? 0 }}</strong></div>
      </div>
      <ul class="plain-list">
        <li v-for="[status, count] in tasksByStatus" :key="status"><span>{{ taskStatusLabel[status as keyof typeof taskStatusLabel] ?? status }}</span><strong>{{ count }} 个任务</strong></li>
      </ul>
    </section>
    <section class="section-block">
      <header class="section-heading"><div><h2>导出部署清单</h2></div></header>
      <form class="stacked-form" @submit.prevent="exportDeployment">
        <label>算法版本<select v-model="exportAlgorithmId" required><option value="" disabled>选择算法版本</option><option v-for="item in algorithms" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }}</option></select></label>
        <label>部署方式<select v-model="exportBackend"><option value="docker">Docker Compose</option><option value="bentoml">BentoML</option><option value="kserve">KServe</option></select></label>
        <button class="primary-button" :disabled="!exportAlgorithmId">生成清单</button>
      </form>
      <template v-if="exportFile">
        <div class="file-tabs"><button v-for="name in Object.keys(exportFiles)" :key="name" type="button" :class="{ active: exportFile === name }" @click="exportFile = name">{{ name }}</button><button type="button" class="text-button" @click="copyExport">复制</button></div>
        <pre class="code-block">{{ exportFiles[exportFile] }}</pre>
      </template>
    </section>
  </div>
</template>
