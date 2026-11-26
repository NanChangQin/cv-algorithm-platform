<script setup lang="ts">
import TrainingBatchActions from '@/components/training/TrainingBatchActions.vue'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { activeTrainingStatuses, formatMetric, progressRatio, trainingApi, trainingStatusLabel, trainingStatusTone, type TrainingRunSummary } from '@/api/training'
import { errorMessage } from '@/utils/errors'
import { relativeTime } from '@/utils/presentation'
import { executionLabels, trainingAlgorithm, trainingExecution, trainingTaskLabel, trainingTaskTypes } from '@/utils/trainingTasks'
const runs = shallowRef<TrainingRunSummary[]>([])
const loading = ref(false)
const error = ref('')
const query = ref('')
const status = ref('all')
const kind = ref('all')
const taskType = ref('all')
const algorithm = ref('all')
const algorithms = computed(() => [...new Set(runs.value.map(trainingAlgorithm))].sort())
const selectedRuns = ref<string[]>([])
const deletedIds = new Set<string>()
const page = ref(1)
const pageSize = ref(10)
const batchActions = ref<InstanceType<typeof TrainingBatchActions> | null>(null)
const active = (run: TrainingRunSummary) => activeTrainingStatuses.includes(run.status)
const counts = computed(() => ({ active: runs.value.filter(active).length, completed: runs.value.filter((run) => run.status === 'completed').length, failed: runs.value.filter((run) => run.status === 'failed').length }))
const filtered = computed(() => runs.value.filter((run) => {
  const matchesStatus = status.value === 'all' || (status.value === 'active' ? active(run) : run.status === status.value)
  const operation = trainingExecution(run)
  const search = `${run.name} ${run.progress?.architecture ?? run.model}`.toLowerCase()
  return matchesStatus && (taskType.value === 'all' || taskType.value === (run.task_type ?? 'object_detection')) && (algorithm.value === 'all' || algorithm.value === trainingAlgorithm(run)) && (kind.value === 'all' || kind.value === operation) && search.includes(query.value.trim().toLowerCase())
}).slice().sort((a, b) => Number(active(b)) - Number(active(a)) || b.created_at.localeCompare(a.created_at)))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([query, status, kind, taskType, algorithm], () => { page.value = 1; selectedRuns.value = [] })
watch(pageSize, () => { page.value = 1 })
watch(pages, (value) => { page.value = Math.min(page.value, value) })
async function refresh() {
  if (loading.value) return
  loading.value = true
  try { runs.value = (await trainingApi.runs()).filter(run => !deletedIds.has(run.id)); selectedRuns.value = selectedRuns.value.filter(id => runs.value.some(run => run.id === id)); error.value = '' }
  catch (reason) { error.value = errorMessage(reason, '任务列表读取失败') }
  finally { loading.value = false }
}
let timer: number | undefined
let disposed = false
function schedule() {
  timer = window.setTimeout(async () => {
    if (!document.hidden) await refresh()
    if (!disposed) schedule()
  }, 5000)
}
onMounted(async () => { await refresh(); if (!disposed) schedule() })
onBeforeUnmount(() => { disposed = true; window.clearTimeout(timer) })
const allPageSelected = computed(() => visible.value.length > 0 && visible.value.every(run => selectedRuns.value.includes(run.id)))
const somePageSelected = computed(() => visible.value.some(run => selectedRuns.value.includes(run.id)))
function selectPage(event: Event) {
  const ids = visible.value.map(run => run.id)
  selectedRuns.value = (event.target as HTMLInputElement).checked
    ? [...new Set([...selectedRuns.value, ...ids])]
    : selectedRuns.value.filter(id => !ids.includes(id))
}
function openBatch(ids: string[], action: 'delete' | 'download') { void batchActions.value?.open(ids, action) }
function removed(ids: string[]) {
  ids.forEach(id => deletedIds.add(id))
  runs.value = runs.value.filter(run => !ids.includes(run.id))
  selectedRuns.value = selectedRuns.value.filter(id => !ids.includes(id))
}
function percent(run: TrainingRunSummary) { const ratio = progressRatio(run); return ratio === null ? null : Math.round(ratio * 100) }
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>训练任务</h1><p>查看训练与验证任务的状态、进度和结果。</p></div>
    <div class="toolbar-actions">
      <RouterLink :to="{ path: '/evaluations', query: { mode: 'single' } }" class="secondary-button compact-button">独立验证</RouterLink>
      <RouterLink v-if="selectedRuns.length>=2 && selectedRuns.length<=4" :to="{path:'/training/compare',query:{ids:selectedRuns.join(',')}}" class="secondary-button compact-button">对比 {{ selectedRuns.length }} 个实验</RouterLink>
      <RouterLink to="/training/new" class="primary-button compact-button">新建训练</RouterLink>
    </div>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }} <button class="text-button" @click="refresh">重试</button></p>
  <div class="training-overview" aria-label="任务概况">
    <button :class="{ selected: status === 'all' }" :aria-pressed="status === 'all'" @click="status = 'all'"><span>全部任务</span><strong>{{ runs.length }}</strong></button>
    <button :class="{ selected: status === 'active' }" :aria-pressed="status === 'active'" @click="status = 'active'"><span>进行中</span><strong>{{ counts.active }}</strong></button>
    <button :class="{ selected: status === 'completed' }" :aria-pressed="status === 'completed'" @click="status = 'completed'"><span>已完成</span><strong>{{ counts.completed }}</strong></button>
    <button :class="{ selected: status === 'failed' }" :aria-pressed="status === 'failed'" @click="status = 'failed'"><span>失败</span><strong>{{ counts.failed }}</strong></button>
  </div>
  <section class="section-block training-task-panel" aria-label="训练任务列表">
    <header class="section-heading"><div><h2>任务列表</h2><span class="count-badge">{{ filtered.length }}</span></div><button type="button" class="text-button" :disabled="loading" @click="refresh">刷新</button></header>
    <div class="training-list-filters">
      <label class="training-filter-search">搜索<input v-model="query" class="training-search" type="search" aria-label="搜索训练任务" placeholder="任务名称或模型" /></label>
      <label>任务类型<select v-model="taskType" aria-label="任务类型"><option value="all">全部任务类型</option><option v-for="item in trainingTaskTypes" :key="item.key" :value="item.key" :disabled="!item.available">{{ item.label }}{{ item.available ? '' : ' · 待接入' }}</option></select></label>
      <label>算法<select v-model="algorithm" aria-label="算法筛选"><option value="all">全部算法</option><option v-for="item in algorithms" :key="item" :value="item">{{ item }}</option></select></label>
      <label>执行方式<select v-model="kind" aria-label="执行方式"><option value="all">全部执行方式</option><option v-for="(label, key) in executionLabels" :key="key" :value="key">{{ label }}{{ key === 'legacy' ? '（历史记录）' : '' }}</option></select></label>
      <label>任务状态<select v-model="status" aria-label="任务状态"><option value="all">全部状态</option><option value="active">进行中</option><option value="completed">已完成</option><option value="stopped">已停止</option><option value="failed">失败</option></select></label>
    </div>
    <TrainingBatchActions ref="batchActions" @removed="removed" />
    <div v-if="runs.length" class="training-bulk-toolbar">
      <label><input type="checkbox" :checked="allPageSelected" :indeterminate="somePageSelected && !allPageSelected" :disabled="!visible.length" aria-label="全选本页任务" @change="selectPage" />全选本页</label>
      <span role="status">已选 {{ selectedRuns.length }} 项</span>
      <button v-if="filtered.length > 0 && selectedRuns.length !== filtered.length" type="button" class="text-button" @click="selectedRuns = filtered.map(run => run.id)">选择全部 {{ filtered.length }} 项筛选结果</button>
      <template v-if="selectedRuns.length"><button type="button" class="secondary-button compact-button" :disabled="batchActions?.active || selectedRuns.length > 1000" @click="openBatch(selectedRuns, 'download')">批量下载</button><button type="button" class="secondary-button compact-button bulk-delete" :disabled="batchActions?.active || selectedRuns.length > 1000" @click="openBatch(selectedRuns, 'delete')">批量删除</button><button type="button" class="text-button" @click="selectedRuns=[]">取消选择</button></template>
    </div>
    <p v-if="selectedRuns.length > 1000" class="alert">单次最多处理 1000 项，请缩小筛选范围。</p>
    <div v-if="loading && !runs.length" class="empty-state">正在读取任务…</div>
    <div v-else-if="!runs.length" class="empty-state"><strong>开始第一次训练</strong><p>准备好图片与标注后，新建任务并选择算法。</p><div class="toolbar-actions"><RouterLink to="/training/datasets" class="secondary-button compact-button">准备数据集</RouterLink><RouterLink to="/training/new" class="primary-button compact-button">新建训练</RouterLink></div></div>
    <p v-else-if="!filtered.length" class="empty-state">没有符合筛选条件的任务。</p>
    <ul v-else class="training-list">
      <li v-for="run in visible" :key="run.id" class="training-selectable-row">
        <input v-model="selectedRuns" type="checkbox" :value="run.id" :aria-label="`选择 ${run.name}`" />
        <RouterLink :to="`/training/${run.id}`" class="training-task-row">
          <span class="training-title"><strong>{{ run.name }}</strong><small>{{ trainingTaskLabel(run.task_type ?? 'object_detection') }} · {{ executionLabels[trainingExecution(run)] }} · {{ (run.progress?.architecture ?? run.model).replace('.pt', '') }} · {{ run.node_id }}</small></span>
          <span :class="`task-status task-${trainingStatusTone[run.status]}`"><i class="status-dot" />{{ trainingStatusLabel[run.status] }}</span>
          <span class="training-task-metric"><template v-if="active(run)"><progress v-if="percent(run) !== null" :value="percent(run) ?? 0" max="100" :aria-label="`${run.name} 进度`" /><small>{{ run.progress?.epochs ? `${run.progress.epoch ?? 0} / ${run.progress.epochs} 轮` : '等待执行' }}</small></template><template v-else><small>最佳 mAP50-95</small><strong>{{ formatMetric(run.best?.map50_95) }}{{ run.best?.map50_95 != null ? '%' : '' }}</strong></template></span>
          <small class="training-time">{{ relativeTime(run.created_at) }}</small><span class="training-open">查看详情 →</span>
        </RouterLink>
        <div class="training-row-actions"><button type="button" class="text-button" :disabled="batchActions?.active" :aria-label="`下载 ${run.name}`" @click="openBatch([run.id], 'download')">下载</button><details><summary :aria-label="`更多操作 ${run.name}`">更多</summary><button type="button" class="text-button bulk-delete" :disabled="active(run) || batchActions?.active" :title="active(run) ? '请先停止训练再删除' : '删除任务与结果'" :aria-label="`删除 ${run.name}`" @click="openBatch([run.id], 'delete')">删除</button></details></div>
      </li>
    </ul>
    <p v-if="runs.length" class="muted-copy">支持跨页选择；修改筛选条件会清空选择。勾选 2–4 项时可进行实验对比。</p>
    <nav v-if="filtered.length" class="training-pagination" aria-label="训练任务分页"><span>共 {{ filtered.length }} 项</span><label>每页 <select v-model.number="pageSize" aria-label="每页任务数量"><option :value="10">10 条</option><option :value="20">20 条</option><option :value="50">50 条</option></select></label><button class="secondary-button compact-button" :disabled="page === 1" @click="page--">上一页</button><span>{{ page }} / {{ pages }}</span><button class="secondary-button compact-button" :disabled="page === pages" @click="page++">下一页</button></nav>
  </section>
</template>

<style scoped>
.training-selectable-row { display:flex; align-items:center; gap:12px; }
.training-selectable-row .training-task-row { flex:1; min-width:0; }
.training-bulk-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:12px; border:1px solid var(--line); border-radius:8px; background:var(--surface-2); padding:12px; margin-bottom:16px; font-size:13px; }
.training-bulk-toolbar label { display:flex; align-items:center; gap:8px; cursor:pointer; }.training-bulk-toolbar input,.training-selectable-row >input { width:18px; height:18px; flex-shrink:0; accent-color:var(--accent); }
.bulk-delete { color:var(--danger); }.training-row-actions { display:flex; gap:12px; align-items:center; flex-shrink:0; font-size:13px; padding-right:12px; }.training-row-actions >button,.training-row-actions summary { min-height:40px; display:flex; align-items:center; }.training-row-actions details { position:relative; }.training-row-actions details[open] >button { position:absolute; z-index:2; right:0; top:40px; min-width:100px; padding:12px; border:1px solid var(--line); border-radius:8px; background:var(--surface); box-shadow:var(--shadow); }.training-pagination select { width:auto; min-height:36px; }
@media(max-width:1100px) { .training-selectable-row { flex-wrap:wrap; }.training-row-actions { width:100%; padding:0 0 8px 30px; justify-content:flex-end; } }
</style>
