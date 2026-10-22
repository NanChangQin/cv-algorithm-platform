<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  activeDeploymentStatuses, deploymentsApi, deploymentStatusLabel, deploymentStatusTone, type Deployment,
} from '@/api/deployments'
import { systemApi, type GpuInfo } from '@/api/system'
import AppIcon from '@/components/layout/AppIcon.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { toast } from '@/composables/useToast'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, nodeLabel } from '@/utils/presentation'

const route = useRoute()
const { algorithms, availableAlgorithms } = useAlgorithms()
const deployments = ref<Deployment[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const gpus = ref<GpuInfo[]>([])
const loading = ref(false)
const error = ref('')
const pendingId = ref('')

const creating = ref(false)
const algorithmId = ref('')
const nodeId = ref('')
const gpuId = ref('')
const submitting = ref(false)

const chosenAlgorithm = computed(() => availableAlgorithms.value.find((item) => item.id === algorithmId.value))
const nodeGpus = computed(() => gpus.value.filter((gpu) => gpu.node_id === nodeId.value))
const algorithmNames = computed(() => Object.fromEntries(algorithms.value.map((item) => [item.id, `${item.name} v${item.version}`])))
const formIssue = computed(() => {
  if (!algorithmId.value) return '请选择要部署的算法版本'
  if (!nodeId.value) return '请选择服务器'
  if (chosenAlgorithm.value?.device === 'gpu' && !nodeGpus.value.length) return '所选服务器没有检测到 GPU'
  return ''
})
const hasActive = computed(() => deployments.value.some((item) => activeDeploymentStatuses.includes(item.status)))
/** 按算法版本分组，同一版本部署在多台服务器时放在一起 */
const groups = computed(() => {
  const grouped = new Map<string, Deployment[]>()
  for (const item of deployments.value) grouped.set(item.algorithm_version_id, [...(grouped.get(item.algorithm_version_id) ?? []), item])
  return [...grouped.entries()].map(([id, items]) => ({ id, name: algorithmNames.value[id] ?? '算法版本', items }))
})

async function refresh() {
  loading.value = true
  try {
    deployments.value = await deploymentsApi.list()
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

async function submit() {
  if (formIssue.value || submitting.value) return
  submitting.value = true
  try {
    await deploymentsApi.create({
      algorithm_version_id: algorithmId.value,
      node_id: nodeId.value,
      gpu_device_ids: gpuId.value ? [gpuId.value] : [],
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
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div>
      <h1>模型部署</h1>
      <p>把算法版本部署到指定服务器的指定 GPU。平台会自动检查目标服务器的镜像和权重，缺少时从其他服务器传输或用算法包构建，然后启动实例；部署的实例不会被空闲回收，推理时可以直接选它。</p>
    </div>
    <button v-if="!creating" type="button" class="primary-button compact-button" @click="startCreating()">新建部署</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section v-if="creating" class="section-block training-form" aria-labelledby="new-deployment-title">
    <header class="section-heading"><div><h2 id="new-deployment-title">新建部署</h2></div><button type="button" class="text-button" @click="creating = false">取消</button></header>
    <div class="training-form-grid deployment-form-grid">
      <label class="field-label">算法版本
        <select v-model="algorithmId">
          <option v-if="!availableAlgorithms.length" value="">没有可用的算法版本</option>
          <option v-for="item in availableAlgorithms" :key="item.id" :value="item.id">{{ item.name }} v{{ item.version }} · {{ item.device.toUpperCase() }}</option>
        </select>
      </label>
      <label class="field-label">服务器
        <select v-model="nodeId">
          <option v-for="node in nodes" :key="node.id" :value="node.id">{{ nodeLabel(node.id, nodes) }}</option>
        </select>
      </label>
      <label v-if="chosenAlgorithm?.device !== 'cpu'" class="field-label">GPU
        <select v-model="gpuId" :disabled="!nodeGpus.length">
          <option value="">自动（空闲显存最多）</option>
          <option v-for="gpu in nodeGpus" :key="gpu.index" :value="String(gpu.index)">
            GPU {{ gpu.index }} · {{ gpu.name }} · 空闲 {{ ((gpu.memory_total_mb - gpu.memory_used_mb) / 1024).toFixed(1) }} GB
          </option>
        </select>
      </label>
    </div>
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
            <small>{{ item.created_by }} 部署于 {{ formatDateTime(item.created_at) }}<template v-if="item.started_at"> · 启动于 {{ formatDateTime(item.started_at) }}</template></small>
          </div>
          <span :class="`task-status task-${deploymentStatusTone[item.status]}`"><i class="status-dot" />{{ deploymentStatusLabel[item.status] }}</span>
        </header>
        <ol class="deployment-steps">
          <li v-for="step in item.steps" :key="step.key" :class="`step-${step.status}`">
            <span class="step-icon"><AppIcon :name="stepIcon[step.status] ?? 'plus'" /></span>
            <div><strong>{{ step.label }}</strong><small v-if="step.detail">{{ step.detail }}</small></div>
          </li>
        </ol>
        <p v-if="item.status === 'failed' && item.error" class="alert" role="alert">{{ item.error }}</p>
        <div class="toolbar-actions">
          <RouterLink v-if="item.status === 'running'" class="primary-button compact-button" :to="{ path: '/', query: { algorithm: item.algorithm_version_id, deployment: item.id } }">去工作台测试</RouterLink>
          <button v-if="item.status === 'running' || item.status === 'queued' || item.status === 'preparing'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'stop')">停止</button>
          <button v-if="item.status === 'stopped' || item.status === 'failed'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'start')">启动</button>
          <button v-if="item.status !== 'preparing' && item.status !== 'stopping'" type="button" class="secondary-button compact-button" :disabled="pendingId === item.id" @click="act(item, 'redeploy')">重新部署</button>
          <button v-if="item.status !== 'preparing' && item.status !== 'stopping'" type="button" class="text-button" :disabled="pendingId === item.id" @click="act(item, 'remove')">删除</button>
        </div>
      </li>
    </ul>
  </section>
</template>
