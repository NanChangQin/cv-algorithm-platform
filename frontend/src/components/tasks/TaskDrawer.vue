<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, toRef, watch } from 'vue'

import { assetsApi } from '@/api/assets'
import { tasksApi } from '@/api/tasks'
import AppIcon from '@/components/layout/AppIcon.vue'
import ResultViewer from '@/components/results/ResultViewer.vue'
import { useTaskWatch } from '@/composables/useTaskWatch'
import { toast } from '@/composables/useToast'
import type { Algorithm } from '@/types/algorithm'
import type { ImageAsset } from '@/types/asset'
import type { InferenceTask } from '@/types/task'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, nodeLabel, taskElapsed } from '@/utils/presentation'
import { taskStatusLabel } from '@/utils/status'
import TaskProgress from './TaskProgress.vue'

const props = withDefaults(defineProps<{
  taskId: string
  algorithms: Algorithm[]
  nodes?: Array<{ id: string; name: string }>
  asset?: ImageAsset | null
}>(), { nodes: () => [], asset: null })
const emit = defineEmits<{ close: []; updated: [task: InferenceTask]; open: [taskId: string] }>()

const { task, result, error, terminal, replace } = useTaskWatch(toRef(props, 'taskId'))
const pending = ref(false)
const closeButton = ref<HTMLButtonElement | null>(null)
const algorithm = computed(() => props.algorithms.find((item) => item.id === task.value?.algorithm_version_id))
const device = computed(() => {
  const value = task.value
  if (!value) return ''
  if (value.execution_node_id) return value.execution_gpu_device_ids.length ? `GPU ${value.execution_gpu_device_ids.join(', ')}` : 'CPU'
  return value.gpu_device_ids.length ? `GPU ${value.gpu_device_ids.join(', ')}` : '自动分配'
})
/** 参数按算法定义的顺序显示，并用参数的中文名 */
const parameters = computed(() => {
  const values = task.value?.parameters ?? {}
  const specs = algorithm.value?.parameters ?? {}
  const keys = [...Object.keys(specs).filter((key) => key in values), ...Object.keys(values).filter((key) => !(key in specs))]
  return keys.map((key) => ({ key, title: specs[key]?.title ?? key, value: String(values[key]) }))
})

watch(task, (value) => { if (value) emit('updated', value) })

async function act(action: 'cancel' | 'retry') {
  if (!task.value || pending.value) return
  pending.value = true
  try {
    const updated = await tasksApi[action](task.value.id)
    if (action === 'retry') {
      toast.success('已重新提交任务')
      emit('open', updated.id)
    } else {
      replace(updated)
      toast.info('任务已取消')
    }
  } catch (reason) {
    toast.error(errorMessage(reason, '操作失败，请重试'))
  } finally { pending.value = false }
}
function downloadJson() {
  if (!result.value) return
  const link = document.createElement('a')
  link.download = `result-${props.taskId}.json`
  link.href = URL.createObjectURL(new Blob([JSON.stringify(result.value, null, 2)], { type: 'application/json' }))
  link.click()
  URL.revokeObjectURL(link.href)
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  void nextTick(() => closeButton.value?.focus())
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="drawer-layer">
    <button type="button" class="drawer-backdrop" tabindex="-1" aria-label="关闭任务详情" @click="emit('close')" />
    <aside class="drawer" role="dialog" aria-modal="true" :aria-label="`任务 ${taskId.slice(0, 8)}`">
      <header class="drawer-header">
        <div>
          <span v-if="task" :class="`task-status task-${task.status}`"><i class="status-dot" /> {{ taskStatusLabel[task.status] }}</span>
          <h2>{{ algorithm?.name ?? '推理任务' }}</h2>
          <p class="muted-copy">
            <template v-if="asset">{{ asset.original_name }} · </template><span class="mono">#{{ taskId.slice(0, 8) }}</span>
          </p>
        </div>
        <button ref="closeButton" type="button" class="icon-button" aria-label="关闭" @click="emit('close')"><AppIcon name="close" /></button>
      </header>
      <div class="drawer-body">
        <p v-if="error" class="alert" role="alert">{{ error }}</p>
        <div v-if="!task" class="empty-state">{{ error ? '暂时无法读取任务。' : '正在加载任务…' }}</div>
        <template v-else>
          <ResultViewer
            v-if="task.status === 'completed' && result"
            compact
            :result="result"
            :image-url="task.asset_id ? assetsApi.contentUrl(task.asset_id) : ''"
          />
          <div v-else-if="task.status === 'completed'" class="empty-state">正在读取推理结果…</div>
          <div v-else class="viewer-pending">
            <img v-if="task.asset_id" :src="assetsApi.thumbnailUrl(task.asset_id, 640)" :alt="asset?.original_name ?? '输入图片'" />
            <TaskProgress :task="task" />
          </div>
          <dl class="drawer-meta">
            <div><dt>执行位置</dt><dd>{{ nodeLabel(task.execution_node_id ?? task.node_id, nodes) }} · {{ device }}</dd></div>
            <div><dt>用时</dt><dd>{{ taskElapsed(task) }}</dd></div>
            <div><dt>创建时间</dt><dd>{{ formatDateTime(task.created_at) }}</dd></div>
            <div v-if="task.retry_of"><dt>重试自</dt><dd><button type="button" class="text-button mono" @click="emit('open', task.retry_of)">#{{ task.retry_of.slice(0, 8) }}</button></dd></div>
            <div v-if="parameters.length" class="wide">
              <dt>运行参数</dt>
              <dd class="param-chips"><span v-for="item in parameters" :key="item.key">{{ item.title }} <strong>{{ item.value }}</strong></span></dd>
            </div>
          </dl>
        </template>
      </div>
      <footer class="drawer-footer">
        <RouterLink class="secondary-button" :to="`/tasks/${taskId}`">完整页面</RouterLink>
        <button v-if="result" type="button" class="secondary-button" @click="downloadJson">下载 JSON</button>
        <button v-if="task && !terminal" type="button" class="secondary-button danger-button" :disabled="pending" @click="act('cancel')">取消任务</button>
        <button v-if="task && ['failed', 'cancelled'].includes(task.status)" type="button" class="secondary-button" :disabled="pending" @click="act('retry')">重试</button>
        <RouterLink v-if="task" class="primary-button compact-button" :to="{ path: '/', query: { from_task: task.id } }">复用配置</RouterLink>
      </footer>
    </aside>
  </div>
</template>
