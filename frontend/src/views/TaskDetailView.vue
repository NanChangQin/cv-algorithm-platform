<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { systemApi } from '@/api/system'
import { tasksApi } from '@/api/tasks'
import ResultViewer from '@/components/results/ResultViewer.vue'
import TaskProgress from '@/components/tasks/TaskProgress.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { useTaskWatch } from '@/composables/useTaskWatch'
import { toast } from '@/composables/useToast'
import type { ImageAsset } from '@/types/asset'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, nodeLabel, taskElapsed } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const { algorithms, availableAlgorithms } = useAlgorithms()
const taskId = computed(() => String(route.params.id))
const { task, result, error, terminal, reload, replace } = useTaskWatch(taskId)
const asset = ref<ImageAsset | null>(null)
const nodes = ref<Array<{ id: string; name: string }>>([])
const pending = ref(false)
const algorithm = computed(() => algorithms.value.find((item) => item.id === task.value?.algorithm_version_id))
const placement = computed(() => {
  const value = task.value
  if (!value) return ''
  if (value.status === 'completed' && !value.execution_node_id && !value.node_id) return '未记录位置'
  return nodeLabel(value.execution_node_id ?? value.node_id, nodes.value)
})
const device = computed(() => {
  const value = task.value
  if (!value) return ''
  if (value.execution_node_id) return value.execution_gpu_device_ids.length ? `GPU ${value.execution_gpu_device_ids.join(', ')}` : 'CPU'
  if (value.status === 'completed') return '未记录设备'
  return value.gpu_device_ids.length ? `GPU ${value.gpu_device_ids.join(', ')}` : '自动分配'
})
/** 参数按算法定义的顺序显示，并用参数的中文名 */
const parameters = computed(() => {
  const values = task.value?.parameters ?? {}
  const specs = algorithm.value?.parameters ?? {}
  const keys = [...Object.keys(specs).filter((key) => key in values), ...Object.keys(values).filter((key) => !(key in specs))]
  return keys.map((key) => ({ key, title: specs[key]?.title ?? key, value: String(values[key]) }))
})

watch(() => task.value?.asset_id, async (id) => {
  if (asset.value?.id === id) return
  asset.value = null
  if (!id) return
  try { asset.value = await assetsApi.get(id) } catch { /* 原图信息只是补充展示 */ }
}, { immediate: true })
onMounted(async () => {
  try { nodes.value = (await systemApi.executionResources()).nodes } catch { /* 资源发现离线时显示记录的节点 ID */ }
})

function downloadJson() {
  if (!result.value) return
  const link = document.createElement('a')
  link.download = `result-${taskId.value}.json`
  link.href = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' }))
  link.click()
  URL.revokeObjectURL(link.href)
}
async function copyId() {
  try {
    await navigator.clipboard.writeText(taskId.value)
    toast.success('已复制任务 ID')
  } catch {
    toast.info(taskId.value)
  }
}
async function act(action: 'cancel' | 'retry') {
  if (pending.value) return
  pending.value = true
  try {
    const updated = await tasksApi[action](taskId.value)
    if (action === 'retry') {
      toast.success('已重新提交任务')
      await router.push(`/tasks/${updated.id}`)
    } else {
      replace(updated)
      toast.info('任务已取消')
    }
  } catch (reason) {
    toast.error(errorMessage(reason, '操作失败，请重试'))
  } finally { pending.value = false }
}
</script>

<template>
  <RouterLink class="back-link" to="/tasks">← 返回任务中心</RouterLink>
  <header class="page-heading page-heading-actions detail-heading">
    <div>
      <h1>{{ algorithm?.name ?? '推理任务' }}</h1>
      <p class="detail-meta">
        <span v-if="task" :class="`task-status task-${task.status}`"><i class="status-dot" /> {{ taskStatusLabel[task.status] }}</span>
        <span v-if="task">{{ placement }} · {{ device }}</span>
        <span v-if="task">用时 {{ taskElapsed(task) }}</span>
        <button type="button" class="task-short-id mono copy-id" title="复制完整任务 ID" @click="copyId">#{{ taskId.slice(0, 8) }}</button>
      </p>
    </div>
    <div class="toolbar-actions">
      <button v-if="result" type="button" class="secondary-button" @click="downloadJson">下载 JSON</button>
      <RouterLink
        v-if="task?.asset_id && availableAlgorithms.length > 1"
        class="secondary-button"
        :to="{ path: '/compare', query: { asset: task.asset_id, algorithms: task.algorithm_version_id } }"
      >对比其他算法</RouterLink>
      <button v-if="task && !terminal" type="button" class="secondary-button danger-button" :disabled="pending" @click="act('cancel')">取消任务</button>
      <button v-if="task && ['failed', 'cancelled'].includes(task.status)" type="button" class="secondary-button" :disabled="pending" @click="act('retry')">重试任务</button>
      <RouterLink v-if="task" class="primary-button compact-button" :to="{ path: '/', query: { from_task: task.id } }">复用配置</RouterLink>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }} <button type="button" class="text-button" @click="reload">重新加载</button></p>
  <div v-if="!task" class="empty-state">{{ error ? '暂时无法读取任务，请重试。' : '正在加载任务…' }}</div>
  <template v-else>
    <section class="detail-result">
      <ResultViewer
        v-if="task.status === 'completed' && result"
        :result="result"
        :image-url="task.asset_id ? assetsApi.contentUrl(task.asset_id) : ''"
      />
      <div v-else-if="task.status === 'completed'" class="empty-state">正在读取推理结果…</div>
      <div v-else class="viewer-pending">
        <img v-if="task.asset_id" :src="assetsApi.thumbnailUrl(task.asset_id, 640)" :alt="asset?.original_name ?? '输入图片'" />
        <TaskProgress :task="task" />
      </div>
    </section>
    <div class="detail-cards">
      <section class="detail-card">
        <h2>输入图片</h2>
        <div v-if="asset" class="detail-input">
          <img :src="assetsApi.thumbnailUrl(asset.id, 160)" :alt="asset.original_name" />
          <div>
            <strong :title="asset.original_name">{{ asset.original_name }}</strong>
            <small>{{ asset.width }} × {{ asset.height }} 像素</small>
            <span>
              <a class="text-button" :href="assetsApi.contentUrl(asset.id)" target="_blank" rel="noopener">查看原图</a>
              <RouterLink class="text-button" :to="{ path: '/', query: { asset: asset.id } }">用这张图再推理</RouterLink>
            </span>
          </div>
        </div>
        <p v-else class="muted-copy">{{ task.asset_id ? '正在读取图片信息…' : '这个任务没有记录输入图片。' }}</p>
      </section>
      <section class="detail-card">
        <h2>运行参数</h2>
        <dl v-if="parameters.length" class="parameter-list">
          <div v-for="item in parameters" :key="item.key"><dt>{{ item.title }}</dt><dd class="mono">{{ item.value }}</dd></div>
        </dl>
        <p v-else class="muted-copy">使用算法默认参数。</p>
      </section>
      <section class="detail-card">
        <h2>任务信息</h2>
        <dl class="parameter-list">
          <div><dt>算法版本</dt><dd>{{ algorithm ? `v${algorithm.version}` : '—' }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatDateTime(task.created_at) }}</dd></div>
          <div><dt>完成时间</dt><dd>{{ formatDateTime(task.completed_at ?? task.cancelled_at) }}</dd></div>
          <div><dt>算法容器</dt><dd class="mono">{{ task.container_id?.slice(0, 12) ?? '尚未分配' }}</dd></div>
          <div v-if="task.retry_of"><dt>重试自</dt><dd><RouterLink class="text-button mono" :to="`/tasks/${task.retry_of}`">#{{ task.retry_of.slice(0, 8) }}</RouterLink></dd></div>
        </dl>
      </section>
    </div>
  </template>
</template>
