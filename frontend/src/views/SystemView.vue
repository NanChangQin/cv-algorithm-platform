<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { systemApi, type AuditEvent, type GpuInfo } from '@/api/system'
import type { RuntimeInstance } from '@/types/instance'

const gpus = ref<GpuInfo[]>([])
const instances = ref<RuntimeInstance[]>([])
const audits = ref<AuditEvent[]>([])
const selectedLogs = ref<string[]>([])
const error = ref('')

async function refresh() {
  error.value = ''
  try {
    const [gpuData, instanceData, auditData] = await Promise.all([
      systemApi.gpus(),
      systemApi.instances(),
      systemApi.auditLogs(),
    ])
    gpus.value = gpuData
    instances.value = instanceData
    audits.value = auditData
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '系统状态加载失败'
  }
}

async function showLogs(instanceId: string) {
  selectedLogs.value = await systemApi.logs(instanceId)
}

onMounted(refresh)
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">OPERATIONS & GOVERNANCE</span>
    <h1>系统管理</h1>
    <p>查看 GPU、受管容器、容器日志与高风险操作审计记录。</p>
  </header>
  <p v-if="error" class="alert">{{ error }}</p>
  <div class="toolbar"><span>运行节点与 GPU 状态</span><button class="secondary-button" @click="refresh">刷新</button></div>
  <section class="system-grid">
    <article class="system-panel">
      <h2>GPU</h2>
      <div v-if="gpus.length === 0" class="empty-state">未发现可用 NVIDIA GPU</div>
      <div v-for="gpu in gpus" :key="`${gpu.node_id}-${gpu.index}`" class="gpu-card">
        <strong>{{ gpu.name }}</strong><span>{{ gpu.node_name }} · GPU {{ gpu.index }}</span>
        <progress :value="gpu.memory_used_mb" :max="gpu.memory_total_mb" />
        <small>{{ gpu.memory_used_mb }} / {{ gpu.memory_total_mb }} MB · {{ gpu.utilization_percent }}%</small>
      </div>
    </article>
    <article class="system-panel">
      <h2>受管容器</h2>
      <div v-if="instances.length === 0" class="empty-state">暂无容器</div>
      <button v-for="instance in instances" :key="instance.id" class="log-target" @click="showLogs(instance.id)">
        <span>{{ instance.container_name }}</span><small>{{ instance.status }}</small>
      </button>
    </article>
  </section>
  <section v-if="selectedLogs.length" class="build-log">
    <header><strong>容器日志</strong><span>{{ selectedLogs.length }} 行</span></header>
    <pre>{{ selectedLogs.join('\n') }}</pre>
  </section>
  <section class="section-block">
    <header class="section-heading"><div><span>AUDIT</span><h2>操作审计</h2></div></header>
    <div class="audit-table">
      <div class="audit-row audit-head"><span>时间</span><span>操作者</span><span>操作</span><span>状态</span><span>请求 ID</span></div>
      <div v-for="event in audits" :key="event.request_id" class="audit-row">
        <span>{{ new Date(event.timestamp).toLocaleString() }}</span><span>{{ event.actor }}</span>
        <span class="mono">{{ event.method }} {{ event.path }}</span><span>{{ event.status_code }}</span>
        <span class="mono">{{ event.request_id.slice(0, 8) }}</span>
      </div>
    </div>
  </section>
</template>
