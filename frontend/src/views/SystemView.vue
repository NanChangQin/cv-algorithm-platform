<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { systemApi, type AuditEvent, type GpuInfo } from '@/api/system'
import { toast } from '@/composables/useToast'
import type { RuntimeInstance } from '@/types/instance'
import { errorMessage } from '@/utils/errors'
import { nodeLabel, runtimeStatusLabel } from '@/utils/presentation'

const gpus = ref<GpuInfo[]>([])
const instances = ref<RuntimeInstance[]>([])
const audits = ref<AuditEvent[]>([])
const nodes = ref<Array<{ id: string; name: string }>>([])
const logTarget = ref<RuntimeInstance | null>(null)
const logs = ref<string[]>([])
const logsLoading = ref(false)
const error = ref('')
const loading = ref(false)
const methodLabel: Record<string, string> = { POST: '提交', PUT: '更新', PATCH: '修改', DELETE: '删除' }

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [gpuData, instanceData, auditData, resources] = await Promise.all([
      systemApi.gpus(), systemApi.instances(), systemApi.auditLogs(), systemApi.executionResources().catch(() => ({ nodes: [], gpus: [] })),
    ])
    gpus.value = gpuData
    instances.value = instanceData
    audits.value = auditData
    nodes.value = resources.nodes
  } catch (reason) {
    error.value = errorMessage(reason, '系统状态加载失败')
  } finally { loading.value = false }
}

async function showLogs(instance: RuntimeInstance) {
  logTarget.value = instance
  logsLoading.value = true
  try { logs.value = await systemApi.logs(instance.id) } catch (reason) {
    logs.value = []
    toast.error(errorMessage(reason, '读取容器日志失败'))
  } finally { logsLoading.value = false }
}
function usage(gpu: GpuInfo) {
  return gpu.memory_total_mb ? Math.round((gpu.memory_used_mb / gpu.memory_total_mb) * 100) : 0
}

onMounted(refresh)
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>系统管理</h1><p>查看各服务器的 GPU、受管容器与日志，以及写操作的审计记录。</p></div>
    <button class="secondary-button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <section class="system-grid">
    <article class="system-panel">
      <h2>GPU</h2>
      <div v-if="gpus.length === 0" class="empty-state">未发现可用 NVIDIA GPU</div>
      <div v-for="gpu in gpus" :key="`${gpu.node_id}-${gpu.index}`" class="gpu-card">
        <strong>{{ gpu.name }}</strong><span>{{ gpu.node_name }} · GPU {{ gpu.index }}</span>
        <progress :value="gpu.memory_used_mb" :max="gpu.memory_total_mb" />
        <small>显存 {{ (gpu.memory_used_mb / 1024).toFixed(1) }} / {{ (gpu.memory_total_mb / 1024).toFixed(1) }} GB（{{ usage(gpu) }}%）· 利用率 {{ gpu.utilization_percent }}%</small>
      </div>
    </article>
    <article class="system-panel">
      <h2>受管容器</h2>
      <div v-if="instances.length === 0" class="empty-state">暂无容器</div>
      <button v-for="instance in instances" :key="instance.id" class="log-target" :class="{ active: logTarget?.id === instance.id }" @click="showLogs(instance)">
        <span>{{ instance.container_name }}<small>{{ nodeLabel(instance.node_id, nodes) }}</small></span><small :class="['runtime-status', `status-${instance.status}`]">{{ runtimeStatusLabel[instance.status] ?? instance.status }}</small>
      </button>
    </article>
  </section>
  <section v-if="logTarget" class="build-log">
    <header><strong>{{ logTarget.container_name }} 的日志</strong><span><button class="text-button" :disabled="logsLoading" @click="showLogs(logTarget)">{{ logsLoading ? '读取中…' : '刷新' }}</button> · <button class="text-button" @click="logTarget = null">关闭</button></span></header>
    <pre>{{ logs.length ? logs.join('\n') : logsLoading ? '正在读取…' : '没有日志输出' }}</pre>
  </section>
  <section class="section-block">
    <header class="section-heading"><div><h2>操作审计</h2><span class="count-badge">最近 {{ audits.length }} 条</span></div></header>
    <div class="audit-table">
      <div class="audit-row audit-head"><span>时间</span><span>操作者</span><span>操作</span><span>结果</span><span>请求 ID</span></div>
      <div v-for="event in audits" :key="event.request_id" class="audit-row">
        <span>{{ new Date(event.timestamp).toLocaleString('zh-CN') }}</span><span>{{ event.actor }}</span>
        <span><b>{{ methodLabel[event.method] ?? event.method }}</b> <span class="mono">{{ event.path }}</span></span><span :class="event.status_code < 400 ? 'success-text' : 'danger-text'">{{ event.status_code }}</span>
        <span class="mono">{{ event.request_id.slice(0, 8) }}</span>
      </div>
    </div>
  </section>
</template>
