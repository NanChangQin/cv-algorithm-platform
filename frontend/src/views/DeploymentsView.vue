<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  activeDeploymentStatuses,
  deploymentsApi,
  deploymentStatusLabel,
  deploymentStatusTone,
  routeStatusLabel,
  type Deployment,
  type DeploymentRoute,
} from '@/api/deployments'
import { advancedApi } from '@/api/advanced'
import { algorithmsApi } from '@/api/algorithms'
import ParameterForm from '@/components/algorithms/ParameterForm.vue'
import type { Algorithm } from '@/types/algorithm'
import { parameterIssue, defaultParameters, type ParameterValues } from '@/utils/parameters'
import { systemApi, type GpuInfo } from '@/api/system'
import AppIcon from '@/components/layout/AppIcon.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, nodeLabel } from '@/utils/presentation'

const route = useRoute()
const { algorithms, availableAlgorithms } = useAlgorithms()
const deployments = ref<Deployment[]>([])
const routes = ref<DeploymentRoute[]>([])
const deploymentMetrics = ref<Record<string, Record<string, unknown>>>({})
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const loading = ref(false)
const error = ref('')
const pendingId = ref('')
const pendingRouteKey = ref('')

const editingRoute = ref(false)
const routeKey = ref('')
const routeName = ref('')
const routeTargetId = ref('')
const routeDrainSeconds = ref(120)
const routeSubmitting = ref(false)

const creating = ref(false)
const algorithmId = ref('')
const nodeId = ref('')
const gpuId = ref('')
const submitting = ref(false)
const detail = ref<Algorithm | null>(null)
const profileError = ref('')
const cpuCount = ref(1)
const memoryMb = ref<number | ''>('')
const shmMb = ref(64)
const timeoutSeconds = ref(120)
const threads = ref(1)
const warmupRuns = ref(1)
const defaults = ref<ParameterValues>({})
const overrideMode = ref('all')
const allowedOverrides = ref<string[]>([])
watch(algorithmId, async (id) => {
  detail.value = null; profileError.value = ''; defaults.value = {}; allowedOverrides.value = []
  threads.value = 1; warmupRuns.value = 1
  if (!id) return
  try {
    const algorithm = await algorithmsApi.get(id)
    if (algorithmId.value !== id) return
    detail.value = algorithm
    defaults.value = defaultParameters(algorithm.parameters)
    allowedOverrides.value = Object.keys(algorithm.parameters)
  } catch (reason) {
    if (algorithmId.value === id) profileError.value = errorMessage(reason, '读取推理参数失败')
  }
})

const chosenAlgorithm = computed(() => availableAlgorithms.value.find((item) => item.id === algorithmId.value))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === nodeId.value))
const algorithmNames = computed(() => Object.fromEntries(algorithms.value.map((item) => [item.id, `${item.name} v${item.version}`])))
const formIssue = computed(() => {
  if (!algorithmId.value) return '请选择要部署的算法版本'
  if (!nodeId.value) return '请选择服务器'
  if (chosenAlgorithm.value?.device === 'gpu' && !nodeGpus.value.length) return '所选服务器没有检测到 GPU'
  if (!detail.value) return profileError.value || '正在读取算法参数'
  if (!Number.isFinite(cpuCount.value) || cpuCount.value < 0.1 || cpuCount.value > 64) return 'CPU 数量范围 0.1～64'
  if (memoryMb.value !== '' && (!Number.isInteger(memoryMb.value) || memoryMb.value < 256 || memoryMb.value > 262144)) return '内存范围 256～262144 MB'
  if (!Number.isInteger(shmMb.value) || shmMb.value < 16 || shmMb.value > 16384) return '共享内存范围 16～16384 MB'
  if (!Number.isInteger(timeoutSeconds.value) || timeoutSeconds.value < 1 || timeoutSeconds.value > 600) return '超时范围 1～600 秒'
  if (!Number.isInteger(threads.value) || threads.value < 1 || threads.value > 64 || !Number.isInteger(warmupRuns.value) || warmupRuns.value < 1 || warmupRuns.value > 10) return '检查 ORT 线程数和预热次数'
  return profileError.value || parameterIssue(detail.value.parameters, defaults.value)
})
const routeFormIssue = computed(() => {
  const key = routeKey.value.trim()
  if (!/^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/.test(key)) return '服务标识需为 2～64 位小写字母、数字或连字符'
  if (!routeTargetId.value) return '请选择目标部署实例'
  if (!Number.isInteger(routeDrainSeconds.value) || routeDrainSeconds.value < 5 || routeDrainSeconds.value > 3600) return '排空超时范围 5～3600 秒'
  return ''
})
const hasActive = computed(() =>
  deployments.value.some((item) => activeDeploymentStatuses.includes(item.status))
  || routes.value.some((item) => item.rollout_status === 'switching' || item.rollout_status === 'draining'),
)
/** 按算法版本分组，同一版本部署在多台服务器时放在一起 */
const groups = computed(() => {
  const grouped = new Map<string, Deployment[]>()
  for (const item of deployments.value) grouped.set(item.algorithm_version_id, [...(grouped.get(item.algorithm_version_id) ?? []), item])
  return [...grouped.entries()].map(([id, items]) => ({ id, name: algorithmNames.value[id] ?? '算法版本', items }))
})

async function refresh() {
  loading.value = true
  try {
    const [deployed, routeList, metricsResp] = await Promise.all([
      deploymentsApi.list(),
      deploymentsApi.listRoutes ? deploymentsApi.listRoutes().catch(() => [] as DeploymentRoute[]) : Promise.resolve([] as DeploymentRoute[]),
      advancedApi.metrics ? advancedApi.metrics().catch(() => ({} as Record<string, unknown>)) : Promise.resolve({} as Record<string, unknown>),
    ])
    deployments.value = deployed
    routes.value = routeList
    deploymentMetrics.value = ((metricsResp as Record<string, unknown>).deployment_metrics as Record<string, Record<string, unknown>> | undefined) ?? {}
    error.value = ''
  } catch (reason) {
    error.value = errorMessage(reason, '加载部署失败')
  } finally { loading.value = false }
}
async function loadResources() {
  try {
    const resources = await systemApi.executionResources()
    nodes.value = resources.nodes
    gpus.value = resources.gpus
    if (!nodes.value.some((node) => node.id === nodeId.value)) nodeId.value = nodes.value[0]?.id ?? ''
  } catch (reason) {
    toast.error(errorMessage(reason, '读取服务器资源失败'))
  }
}

let timer: number | undefined
function schedule() {
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => { if (hasActive.value) await refresh(); schedule() }, 2000)
}
onMounted(async () => {
  await Promise.all([refresh(), loadResources()])
  schedule()
  if (typeof route.query.algorithm === 'string') startCreating(route.query.algorithm)
})
onBeforeUnmount(() => window.clearTimeout(timer))
watch(nodeId, () => { gpuId.value = '' })

function startCreating(preselected?: string) {
  creating.value = true
  algorithmId.value = preselected ?? availableAlgorithms.value[0]?.id ?? ''
  void loadResources()
}

function openRouteEditor(existing?: DeploymentRoute, targetDeploymentId?: string) {
  editingRoute.value = true
  if (existing) {
    routeKey.value = existing.key
    routeName.value = existing.name
    routeTargetId.value = targetDeploymentId ?? existing.candidate_deployment_id ?? existing.active_deployment_id
    routeDrainSeconds.value = existing.drain_timeout_seconds
    return
  }
  routeKey.value = ''
  routeName.value = ''
  routeTargetId.value = targetDeploymentId
    ?? deployments.value.find((item) => item.status === 'running')?.id
    ?? deployments.value[0]?.id
    ?? ''
  routeDrainSeconds.value = 120
}

async function submitRoute() {
  if (routeFormIssue.value || routeSubmitting.value || !deploymentsApi.saveRoute) return
  routeSubmitting.value = true
  try {
    const saved = await deploymentsApi.saveRoute(routeKey.value.trim(), {
      name: routeName.value.trim() || undefined,
      target_deployment_id: routeTargetId.value,
      drain_timeout_seconds: routeDrainSeconds.value,
    })
    toast.success(saved.rollout_status === 'switching' ? '已登记候选部署，验证就绪后自动切换流量' : '服务入口已更新')
    editingRoute.value = false
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '保存服务入口失败'))
  } finally { routeSubmitting.value = false }
}

async function rollbackRoute(item: DeploymentRoute) {
  if (!deploymentsApi.rollbackRoute) return
  pendingRouteKey.value = item.key
  try {
    await deploymentsApi.rollbackRoute(item.key)
    toast.success(`服务入口「${item.key}」已触发回滚`)
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '回滚服务入口失败'))
  } finally { pendingRouteKey.value = '' }
}

async function removeRoute(item: DeploymentRoute) {
  if (!deploymentsApi.removeRoute || !window.confirm(`删除服务入口「${item.key}」？底层部署实例不会被删除。`)) return
  pendingRouteKey.value = item.key
  try {
    await deploymentsApi.removeRoute(item.key)
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '删除服务入口失败'))
  } finally { pendingRouteKey.value = '' }
}

async function submit() {
  if (formIssue.value || submitting.value) return
  submitting.value = true
  try {
    await deploymentsApi.create({
      algorithm_version_id: algorithmId.value,
      node_id: nodeId.value,
      gpu_device_ids: chosenAlgorithm.value?.device !== 'cpu' && gpuId.value ? [gpuId.value] : [],
      runtime_profile: {
        memory_mb: memoryMb.value === '' ? null : memoryMb.value, cpu_count: cpuCount.value,
        shm_mb: shmMb.value, prediction_timeout_seconds: timeoutSeconds.value,
        threads: detail.value?.lineage?.export_job_id ? threads.value : 1,
        warmup_runs: detail.value?.lineage?.export_job_id ? warmupRuns.value : 1,
        defaults: defaults.value, allowed_overrides: overrideMode.value === 'all' ? null : allowedOverrides.value,
      },
    })
    toast.success('已开始部署')
    creating.value = false
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '部署失败'))
  } finally { submitting.value = false }
}

async function act(deployment: Deployment, action: 'stop' | 'start' | 'redeploy' | 'remove') {
  const prompts: Partial<Record<typeof action, string>> = {
    redeploy: '重新部署会删除现有容器并重新检查镜像和权重，正在执行的推理会失败。继续？',
    remove: '删除这个部署和它的容器？',
  }
  if (prompts[action] && !window.confirm(prompts[action])) return
  pendingId.value = deployment.id
  try {
    await deploymentsApi[action](deployment.id)
    await refresh()
  } catch (reason) {
    toast.error(errorMessage(reason, '操作失败'))
  } finally { pendingId.value = '' }
}

const stepIcon: Record<string, string> = { done: 'check', skipped: 'arrow', failed: 'close', running: 'refresh', pending: 'plus' }
const placement = (item: Deployment) => `${nodeLabel(item.node_id, nodes.value)}${item.gpu_device_ids.length ? ` · GPU ${item.gpu_device_ids.join(', ')}` : ' · CPU'}`
function deploymentSummary(id: string | null) {
  if (!id) return '无'
  const item = deployments.value.find((entry) => entry.id === id)
  if (!item) return id.slice(0, 8)
  return `${algorithmNames.value[item.algorithm_version_id] ?? '算法版本'}（${placement(item)} · ${deploymentStatusLabel[item.status]}）`
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div>
      <h1>模型部署</h1>
      <p>把算法版本部署到指定服务器的指定 GPU。平台会自动检查目标服务器的镜像和权重，缺少时从其他服务器传输或用算法包构建，然后启动实例；可绑定稳定的服务入口实现新版本就绪切换、旧实例在途排空和故障回滚。</p>
    </div>
    <div class="toolbar-actions">
      <button v-if="!editingRoute && deployments.length" type="button" class="secondary-button compact-button" @click="openRouteEditor()">配置服务路由</button>
      <button v-if="!creating" type="button" class="primary-button compact-button" @click="startCreating()">新建部署</button>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section v-if="editingRoute" class="section-block training-form" aria-labelledby="route-editor-title">
    <header class="section-heading"><div><h2 id="route-editor-title">配置服务版本路由</h2></div><button type="button" class="text-button" @click="editingRoute = false">取消</button></header>
    <div class="training-form-grid">
      <label class="field-label">服务标识（service_key）<input v-model="routeKey" type="text" placeholder="例如 helmet-prod" /></label>
      <label class="field-label">服务名称<input v-model="routeName" type="text" placeholder="例如 安全帽线上服务" /></label>
      <label class="field-label">目标部署实例
        <select v-model="routeTargetId">
          <option v-for="item in deployments" :key="item.id" :value="item.id">
            {{ algorithmNames[item.algorithm_version_id] ?? '算法版本' }} · {{ placement(item) }} · {{ deploymentStatusLabel[item.status] }}
          </option>
        </select>
      </label>
      <label class="field-label">旧实例排空超时 秒<input v-model.number="routeDrainSeconds" type="number" min="5" max="3600" /></label>
    </div>
    <p class="muted-copy">若目标部署正在部署或尚未启动，平台会保持当前线上部署继续服务，等目标实例就绪并通过预热验证后再自动切流，旧实例转入排空并在在途请求完成后停止。</p>
    <p v-if="routeFormIssue" class="muted-copy">{{ routeFormIssue }}</p>
    <button type="button" class="primary-button" :disabled="Boolean(routeFormIssue) || routeSubmitting" @click="submitRoute">{{ routeSubmitting ? '正在保存…' : '保存服务路由' }}</button>
  </section>

  <section v-if="routes.length" class="section-block" aria-label="服务版本路由">
    <header class="section-heading"><div><h2>服务版本路由</h2><span class="count-badge">{{ routes.length }}</span></div></header>
    <ul class="deployment-list">
      <li v-for="item in routes" :key="item.key" class="deployment-card">
        <header>
          <div>
            <strong>{{ item.name }}（{{ item.key }}）</strong>
            <small>当前：{{ deploymentSummary(item.active_deployment_id) }}<template v-if="item.candidate_deployment_id"> · 待切换：{{ deploymentSummary(item.candidate_deployment_id) }}</template><template v-if="item.previous_deployment_id"> · 上一版本：{{ deploymentSummary(item.previous_deployment_id) }}</template></small>
          </div>
          <span class="task-status task-completed">{{ routeStatusLabel[item.rollout_status] }}</span>
        </header>
        <p v-if="item.last_error" class="alert" role="alert">{{ item.last_error }}</p>
        <div class="toolbar-actions">
          <RouterLink class="primary-button compact-button" :to="{ path: '/', query: { algorithm: item.active_algorithm_version_id ?? undefined, service: item.key } }">去工作台调用</RouterLink>
          <button type="button" class="secondary-button compact-button" :disabled="pendingRouteKey === item.key" @click="openRouteEditor(item)">切换版本</button>
          <button v-if="item.previous_deployment_id" type="button" class="secondary-button compact-button" :disabled="pendingRouteKey === item.key" @click="rollbackRoute(item)">回滚上一版本</button>
          <button type="button" class="text-button" :disabled="pendingRouteKey === item.key" @click="removeRoute(item)">删除路由</button>
        </div>
      </li>
    </ul>
  </section>

  <section v-if="creating" class="section-block training-form" aria-labelledby="new-deployment-title">
    <header class="section-heading"><div><h2 id="new-deployment-title">新建部署</h2></div><button type="button" class="text-button" @click="creating = false">取消</button></header>
    <div class="training-form-grid deployment-form-grid">
      <label class="field-label">算法版本
        <select v-model="algorithmId">
          <option v-for="item in availableAlgorithms" :key="item.id" :value="item.id">
            {{ item.name }} · v{{ item.version }}（{{ item.device === 'cpu' ? 'CPU' : 'GPU' }}）
          </option>
        </select>
      </label>
      <label class="field-label">目标服务器
        <select v-model="nodeId">
          <option v-for="node in nodes" :key="node.id" :value="node.id">{{ node.name }}（{{ node.id }}）</option>
        </select>
      </label>
      <label v-if="chosenAlgorithm?.device !== 'cpu'" class="field-label">GPU
        <select v-model="gpuId">
          <option value="">自动选择空闲显存最多的一张</option>
          <option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">
            GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ gpu.memory_total_mb - gpu.memory_used_mb }} / {{ gpu.memory_total_mb }} MB
          </option>
        </select>
      </label>
    </div>
    <div class="training-form-grid">
      <label class="field-label">CPU 数量<input v-model.number="cpuCount" type="number" min="0.1" max="64" step="0.1" /></label>
      <label class="field-label">内存上限 MB<input v-model.number="memoryMb" type="number" min="256" max="262144" placeholder="使用算法推荐值" /></label>
      <label class="field-label">共享内存 MB<input v-model.number="shmMb" type="number" min="16" max="16384" /></label>
      <label class="field-label">推理超时 秒<input v-model.number="timeoutSeconds" type="number" min="1" max="600" /></label>
      <template v-if="detail?.lineage?.export_job_id">
        <label class="field-label">ORT CPU 线程数<input v-model.number="threads" type="number" min="1" max="64" /></label>
        <label class="field-label">启动预热次数<input v-model.number="warmupRuns" type="number" min="1" max="10" /></label>
      </template>
    </div>
    <p class="muted-copy">ONNX 适配器使用单 worker、单请求并发、FP32 CPU、单图 batch=1；实例繁忙时返回 429。</p>
    <template v-if="detail">
      <h3>服务默认推理参数</h3>
      <ParameterForm v-model="defaults" :parameters="detail.parameters" @validity="profileError = $event" />
      <label class="field-label">请求参数覆盖<select v-model="overrideMode"><option value="all">允许覆盖全部参数</option><option value="selected">仅允许覆盖勾选参数</option></select></label>
      <fieldset v-if="overrideMode === 'selected'"><legend>允许请求覆盖</legend><label v-for="(spec, key) in detail.parameters" :key="key" class="checkbox-label"><input v-model="allowedOverrides" type="checkbox" :value="key" />{{ spec.title ?? key }}（{{ key }}）</label></fieldset>
    </template>
    <p v-if="formIssue" class="muted-copy">{{ formIssue }}</p>
    <button type="button" class="primary-button" :disabled="Boolean(formIssue) || submitting" @click="submit">{{ submitting ? '正在提交…' : '开始部署' }}</button>
  </section>

  <div v-if="loading && !deployments.length" class="section-block empty-state">正在加载…</div>
  <div v-else-if="!deployments.length" class="section-block empty-state"><strong>还没有部署</strong><p>点右上角“新建部署”，或在算法库的版本上点“部署”。</p></div>
  <section v-for="group in groups" :key="group.id" class="section-block">
    <header class="section-heading"><div><h2>{{ group.name }}</h2><span class="count-badge">{{ group.items.length }}</span></div></header>
    <ul class="deployment-list">
      <li v-for="item in group.items" :key="item.id" class="deployment-card">
        <header>
          <div>
            <strong>{{ placement(item) }}</strong>
            <small>{{ item.created_by }} 部署于 {{ formatDateTime(item.created_at) }}<template v-if="item.started_at"> · 启动于 {{ formatDateTime(item.started_at) }}</template><template v-if="item.in_flight_tasks"> · 在途任务 {{ item.in_flight_tasks }}</template><template v-if="deploymentMetrics[item.id]?.completed_samples"> · 推理 P50/P95/P99 {{ Number(deploymentMetrics[item.id]?.inference_p50_ms ?? 0).toFixed(1) }}/{{ Number(deploymentMetrics[item.id]?.inference_p95_ms ?? 0).toFixed(1) }}/{{ Number(deploymentMetrics[item.id]?.inference_p99_ms ?? 0).toFixed(1) }} ms · QPS {{ Number(deploymentMetrics[item.id]?.qps_1m ?? 0).toFixed(2) }} · 错误率 {{ (Number(deploymentMetrics[item.id]?.error_rate ?? 0) * 100).toFixed(1) }}%</template></small>
          </div>
          <span :class="`task-status task-${deploymentStatusTone[item.status]}`"><i class="status-dot" />{{ deploymentStatusLabel[item.status] }}</span>
        </header>
        <ol class="deployment-steps">
          <li v-for="step in item.steps" :key="step.key" :class="`step-${step.status}`">
            <span class="step-icon"><AppIcon :name="stepIcon[step.status] ?? 'plus'" /></span>
            <div><strong>{{ step.label }}</strong><small v-if="step.detail">{{ step.detail }}</small></div>
          </li>
        </ol>
        <details v-if="item.runtime_profile || item.readiness"><summary>运行配置与模型验证证据</summary><pre class="annotation-example">{{ JSON.stringify({ runtime_profile: item.runtime_profile, readiness: item.readiness }, null, 2) }}</pre></details>
        <p v-if="item.status === 'failed' && item.error" class="alert" role="alert">{{ item.error }}</p>
        <div class="toolbar-actions">
          <RouterLink v-if="item.status === 'running'" class="primary-button compact-button" :to="{ path: '/', query: { algorithm: item.algorithm_version_id, deployment: item.id } }">去工作台测试</RouterLink>
          <button v-if="item.status === 'running' || item.status === 'queued' || item.status === 'preparing' || item.status === 'draining'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'stop')">{{ item.status === 'draining' ? '立即停止' : '停止' }}</button>
          <button v-if="item.status === 'stopped' || item.status === 'failed'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'start')">启动</button>
          <button v-if="item.status !== 'preparing' && item.status !== 'stopping' && item.status !== 'draining'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'redeploy')">重新部署</button>
          <button v-if="item.status !== 'preparing' && item.status !== 'stopping' && item.status !== 'draining'" type="button" class="text-button" :disabled="pendingId === item.id" @click="act(item, 'remove')">删除</button>
        </div>
      </li>
    </ul>
  </section>
</template>
