<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { useAlgorithms } from '@/composables/useAlgorithms'
import { useInstances } from '@/composables/useInstances'
import { systemApi } from '@/api/system'
import { nodeLabel, runtimeStatusLabel } from '@/utils/presentation'

const { algorithms } = useAlgorithms()
const { instances, loading, pendingId, error, refresh, start, stop, remove } = useInstances()
const nodes = ref<Array<{ id: string; name: string }>>([])
onMounted(async () => { try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* Recorded node ids remain available. */ } })

const algorithmNames = computed(() =>
  Object.fromEntries(algorithms.value.map((algorithm) => [algorithm.id, algorithm.name])),
)
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">CONTAINER RUNTIME</span>
    <h1>运行实例</h1>
    <p>按算法隔离模型环境；可查看各服务器上的实例及其 GPU 分配，生命周期由执行服务管理。</p>
  </header>

  <p v-if="error" class="alert">{{ error }}</p>
  <div class="toolbar">
    <span>{{ instances.length }} 个实例</span>
    <button class="secondary-button" @click="refresh">刷新</button>
  </div>

  <section class="runtime-launcher">
    <article v-for="algorithm in algorithms" :key="algorithm.id" class="runtime-card">
      <div>
        <span class="mono">{{ algorithm.image }}</span>
        <strong>{{ algorithm.name }}</strong>
        <small>{{ algorithm.device.toUpperCase() }} · {{ algorithm.version }}</small>
      </div>
      <button
        class="primary-button compact-button"
        :disabled="pendingId === algorithm.id"
        @click="start(algorithm.id)"
      >
        {{ pendingId === algorithm.id ? '启动中…' : '启动 / 复用' }}
      </button>
    </article>
  </section>

  <div v-if="loading && !instances.length" class="empty-state">正在加载…</div>
  <div v-else-if="instances.length === 0" class="empty-state">暂无运行实例</div>
  <div v-else class="instance-table">
    <div class="instance-row instance-head">
      <span>算法</span><span>容器</span><span>状态</span><span>设备</span><span>操作</span>
    </div>
    <div v-for="instance in instances" :key="instance.id" class="instance-row">
      <span class="instance-algorithm">{{ algorithmNames[instance.algorithm_version_id] ?? '未知算法' }}</span>
      <span class="mono instance-container">{{ instance.container_name }}</span>
      <span :class="['runtime-status', `status-${instance.status}`]">{{ runtimeStatusLabel[instance.status] }}</span>
      <span class="instance-placement">{{ nodeLabel(instance.node_id, nodes) }}<small>{{ instance.device.toUpperCase() }}{{ instance.gpu_device_ids.length ? ` ${instance.gpu_device_ids.join(', ')}` : '' }}</small></span>
      <span class="runtime-actions">
        <button
          class="secondary-button"
          :disabled="pendingId === instance.id || instance.status === 'stopped'"
          @click="stop(instance.id)"
        >停止</button>
        <button
          class="secondary-button danger-button"
          :disabled="pendingId === instance.id"
          @click="remove(instance.id)"
        >删除</button>
      </span>
    </div>
  </div>
</template>
