<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { assetsApi } from '@/api/assets'
import { evaluationsApi, type AlgorithmScore, type EvaluationReport, type EvaluationRun } from '@/api/evaluations'
import TaskDrawer from '@/components/tasks/TaskDrawer.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { errorMessage } from '@/utils/errors'
import { bestValue, formatMetric, metricColumns, metricLabel } from '@/utils/metrics'
import { formatDateTime, taskTypeLabel } from '@/utils/presentation'

const route = useRoute()
const { algorithms } = useAlgorithms()
const run = ref<EvaluationRun | null>(null)
const report = ref<EvaluationReport | null>(null)
const error = ref('')
const openTaskId = ref('')
const runId = computed(() => String(route.params.id))
const columns = computed(() => (report.value?.task_type ? metricColumns[report.value.task_type] : []))
const totals = computed(() => {
  const items = report.value?.algorithms ?? []
  const total = items.reduce((sum, item) => sum + item.total, 0)
  const done = items.reduce((sum, item) => sum + item.completed + item.failed, 0)
  return { total, done, failed: items.reduce((sum, item) => sum + item.failed, 0) }
})
const best = computed(() => Object.fromEntries(columns.value.map((key) => [
  key, bestValue(key, (report.value?.algorithms ?? []).map((item) => item.metrics[key])),
])))

/** 只列没拿满分的图片，满分的对找问题没有帮助 */
function imperfect(samples: AlgorithmScore['worst']) {
  return samples.filter((sample) => sample.value < 1)
}
function algorithmName(id: string) {
  const algorithm = algorithms.value.find((item) => item.id === id)
  return algorithm ? `${algorithm.name} v${algorithm.version}` : '已删除的算法'
}

async function load() {
  try {
    const response = await evaluationsApi.report(runId.value)
    run.value = response.run
    report.value = response.report
    error.value = ''
  } catch (reason) {
    error.value = errorMessage(reason, '读取评测报告失败')
  }
}
// 还有任务在运行时每 3 秒刷新一次
const poller = window.setInterval(() => { if (report.value && !report.value.finished && !document.hidden) void load() }, 3000)
onMounted(load)
onUnmounted(() => window.clearInterval(poller))
</script>

<template>
  <RouterLink class="back-link" to="/evaluations">← 返回数据集评测</RouterLink>
  <header class="page-heading">
    <h1>{{ run?.dataset_name ?? '评测报告' }}</h1>
    <p v-if="run">{{ taskTypeLabel[run.task_type] ?? '' }} · {{ run.task_count }} 个推理任务 · {{ formatDateTime(run.created_at) }}</p>
  </header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <div v-if="!report" class="empty-state">{{ error ? '暂时无法读取报告。' : '正在计算指标…' }}</div>
  <template v-else>
    <section v-if="!report.finished" class="batch-progress evaluation-progress" role="status">
      <div><strong>评测进行中：{{ totals.done }}/{{ totals.total }}</strong><span>指标会随任务完成自动更新</span></div>
      <span class="batch-bar"><i :style="{ width: `${totals.total ? totals.done / totals.total * 100 : 0}%` }" /></span>
    </section>
    <p v-if="totals.failed" class="alert" role="alert">有 {{ totals.failed }} 个推理任务失败，没有计入指标。可以在任务中心查看原因。</p>

    <section class="section-block evaluation-table-panel">
      <header class="section-heading"><div><h2>指标对比</h2></div><span class="muted-copy">每列最好的结果加粗高亮</span></header>
      <div class="evaluation-table">
        <table aria-label="算法指标对比">
          <thead>
            <tr><th>算法</th><th v-for="key in columns" :key="key">{{ metricLabel[key] ?? key }}</th><th>已评测</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in report.algorithms" :key="item.algorithm_version_id">
              <th scope="row">{{ algorithmName(item.algorithm_version_id) }}</th>
              <td
                v-for="key in columns"
                :key="key"
                :class="{ best: best[key] !== undefined && item.metrics[key] === best[key] }"
              >{{ formatMetric(key, item.metrics[key]) }}</td>
              <td class="muted-copy">{{ item.completed }}/{{ item.total }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted-copy evaluation-note">
        <template v-if="report.task_type === 'object_detection'">检测框与标注的 IoU ≥ 0.5 且类别相同算正确检出；精确率、召回率、F1 按全部图片累计计算。</template>
        <template v-else-if="report.task_type === 'pose_estimation'">PCK：关键点距离小于图片长边 × 阈值（默认 0.05）算正确。</template>
        <template v-else>指标为所有图片的平均值。</template>
      </p>
    </section>

    <section v-for="item in report.algorithms.filter((entry) => entry.completed)" :key="`worst-${item.algorithm_version_id}`" class="section-block">
      <header class="section-heading">
        <div><h2>{{ algorithmName(item.algorithm_version_id) }} · 表现最差的图片</h2></div>
        <span class="muted-copy">按{{ metricLabel[report.primary_metric] ?? report.primary_metric }}从低到高，点开查看具体结果</span>
      </header>
      <p v-if="!imperfect(item.worst).length" class="muted-copy">已评测的图片全部满分。</p>
      <div v-else class="worst-grid">
        <button v-for="sample in imperfect(item.worst)" :key="sample.task_id" type="button" class="worst-sample" @click="openTaskId = sample.task_id">
          <img :src="assetsApi.thumbnailUrl(sample.asset_id, 320)" alt="" loading="lazy" />
          <span>{{ formatMetric(report.primary_metric, sample.value) }}</span>
        </button>
      </div>
    </section>
  </template>
  <TaskDrawer v-if="openTaskId" :key="openTaskId" :task-id="openTaskId" :algorithms="algorithms" @close="openTaskId = ''" @open="openTaskId = $event" />
</template>
