<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { useAlgorithms } from '@/composables/useAlgorithms'
import { useInstances } from '@/composables/useInstances'
import { toast } from '@/composables/useToast'
import { systemApi } from '@/api/system'
import type { RuntimeInstance } from '@/types/instance'
import { formatDateTime, nodeLabel, runtimeStatusLabel } from '@/utils/presentation'

const { algorithms, availableAlgorithms } = useAlgorithms()
const { instances, loading, pendingId, error, refresh, start, stop, remove } = useInstances()
const nodes = ref<Array<{ id: string; name: string }>>([])
onMounted(async () => { try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* 仍可显示记录的节点 ID */ } })

const algorithmNames = computed(() => Object.fromEntries(algorithms.value.map((algorithm) => [algorithm.id, `${algorithm.name} v${algorithm.version}`])))

async function startAlgorithm(id: string, name: string) {
  await start(id)
  if (!error.value) toast.success(`${name} 的实例已就绪`)
}
async function stopInstance(instance: RuntimeInstance) {
  await stop(instance.id)
  if (!error.value) toast.info('实例已停止，下次推理时会自动启动')
}
async function removeInstance(instance: RuntimeInstance) {
  if (!window.confirm(`删除容器 ${instance.container_name}？正在执行的推理会失败，下次推理时会重新创建。`)) return
  await remove(instance.id)
  if (!error.value) toast.success('实例已删除')
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>运行实例</h1><p>每个算法版本在独立容器中运行。推理时会自动启动或复用实例，空闲一段时间后自动回收；这里可以手动预热或释放资源。</p></div>
    <button class="secondary-button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>

  <section class="section-block">
    <header class="section-heading"><div><h2>预热算法</h2></div><small class="muted-copy">首次推理需要加载模型，提前启动可以减少等待</small></header>
    <div v-if="!availableAlgorithms.length" class="empty-state">没有可用算法。</div>
    <div v-else class="runtime-launcher">
      <article v-for="algorithm in availableAlgorithms" :key="algorithm.id" class="runtime-card">
        <div>
          <span class="mono">{{ algorithm.image }}</span>
          <strong>{{ algorithm.name }}</strong>
          <small>v{{ algorithm.version }} · {{ algorithm.device.toUpperCase() }} · {{ runtimeStatusLabel[algorithm.container_status] ?? '未启动' }}</small>
        </div>
        <button class="primary-button compact-button" :disabled="pendingId === algorithm.id" @click="startAlgorithm(algorithm.id, algorithm.name)">{{ pendingId === algorithm.id ? '启动中…' : algorithm.container_status === 'healthy' ? '已在运行' : '启动' }}</button>
      </article>
    </div>
  </section>

  <section class="section-block">
    <header class="section-heading"><div><h2>全部实例</h2><span class="count-badge">{{ instances.length }}</span></div></header>
    <div v-if="loading && !instances.length" class="empty-state">正在加载…</div>
    <div v-else-if="instances.length === 0" class="empty-state">暂无运行实例</div>
    <div v-else class="instance-table">
      <div class="instance-row instance-head">
        <span>算法</span><span>容器</span><span>状态</span><span>位置</span><span>操作</span>
      </div>
      <div v-for="instance in instances" :key="instance.id" class="instance-row">
        <span class="instance-algorithm">{{ algorithmNames[instance.algorithm_version_id] ?? '未知算法' }}<small>最近使用 {{ formatDateTime(instance.last_used_at) }}</small></span>
        <span class="mono instance-container">{{ instance.container_name }}</span>
        <span :class="['runtime-status', `status-${instance.status}`]" :title="instance.error ?? ''">{{ runtimeStatusLabel[instance.status] ?? instance.status }}</span>
        <span class="instance-placement">{{ nodeLabel(instance.node_id, nodes) }}<small>{{ instance.device.toUpperCase() }}{{ instance.gpu_device_ids.length ? ` ${instance.gpu_device_ids.join(', ')}` : '' }}</small></span>
        <span class="runtime-actions">
          <button class="secondary-button" :disabled="pendingId === instance.id || instance.status === 'stopped'" @click="stopInstance(instance)">停止</button>
          <button class="secondary-button danger-button" :disabled="pendingId === instance.id" @click="removeInstance(instance)">删除</button>
        </span>
      </div>
    </div>
  </section>
</template>
