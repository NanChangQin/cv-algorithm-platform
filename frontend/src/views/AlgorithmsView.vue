<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import { algorithmsApi } from '@/api/algorithms'
import type { AlgorithmTemplateType } from '@/api/algorithms'
import { apiUrl } from '@/api/http'
import AlgorithmCard from '@/components/algorithms/AlgorithmCard.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import type { Algorithm, BuildJob } from '@/types/algorithm'
import { sessionRole } from '@/api/auth'
import { buildProgress, buildStatusLabel } from '@/utils/status'

const { algorithms, loading, error, refresh } = useAlgorithms()
const actionError = ref('')
const buildNotice = ref('')
const importing = ref(false)
const selected = ref<Algorithm | null>(null)
const buildJob = ref<BuildJob | null>(null)
const query = ref('')
const taskType = ref('')
const algorithmStatus = ref('')
const templateType = ref<AlgorithmTemplateType>('object_detection')
const showImportGuide = ref(false)
const templateDownloadUrl = computed(() =>
  apiUrl(`/algorithms/template?task_type=${encodeURIComponent(templateType.value)}`),
)
const filteredAlgorithms = computed(() => algorithms.value.filter((algorithm) =>
  (!query.value || algorithm.name.toLowerCase().includes(query.value.toLowerCase()) || algorithm.key.includes(query.value.toLowerCase()))
  && (!taskType.value || algorithm.task_type === taskType.value)
  && (!algorithmStatus.value || algorithm.status === algorithmStatus.value),
))
const canBuild = computed(() => ['admin', 'developer'].includes(sessionRole.get() ?? 'user'))
const isAdmin = computed(() => sessionRole.get() === 'admin')
let buildPollTimer: number | undefined

async function refreshSelected() {
  await refresh()
  if (!selected.value) return
  selected.value = algorithms.value.find((algorithm) => algorithm.id === selected.value?.id) ?? selected.value
}

async function importPackage(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  actionError.value = ''
  buildNotice.value = ''
  buildJob.value = null
  try {
    const imported = await algorithmsApi.importPackage(file)
    selected.value = imported
    buildNotice.value = `${imported.name} 已上传，正在提交自动构建…`
    await startBuild(imported)
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '上传或提交自动构建失败'
    buildNotice.value = ''
  } finally {
    importing.value = false
    input.value = ''
  }
}

async function buildAlgorithm() {
  if (!selected.value) return
  actionError.value = ''
  buildNotice.value = ''
  try {
    await startBuild(selected.value)
  } catch (reason) {
    actionError.value = reason instanceof Error ? reason.message : '提交构建失败'
  }
}

async function startBuild(algorithm: Algorithm) {
  if (buildPollTimer) window.clearTimeout(buildPollTimer)
  buildJob.value = await algorithmsApi.build(algorithm.id)
  buildNotice.value = `${algorithm.name} 正在后台构建并验收，请勿重复上传。`
  await refreshSelected()
  pollBuild(buildJob.value.id)
}

function pollBuild(jobId: string) {
  buildPollTimer = window.setTimeout(async () => {
    try {
      const job = await algorithmsApi.buildJob(jobId)
      if (buildJob.value?.id !== jobId) return
      buildJob.value = job
      actionError.value = ''
      await refreshSelected()
      if (job.status === 'completed') {
        buildNotice.value = `${selected.value?.name ?? '算法'} 构建和协议验收通过，已自动发布为可用版本。`
        return
      }
      if (job.status === 'failed') {
        buildNotice.value = ''
        actionError.value = job.error_message ?? '算法构建失败，请查看下方构建日志后重新构建。'
        return
      }
      pollBuild(jobId)
    } catch (reason) {
      actionError.value = reason instanceof Error ? `${reason.message}；后台构建不受影响，正在重试获取进度。` : '获取构建进度失败，正在重试。'
      pollBuild(jobId)
    }
  }, 1500)
}

onBeforeUnmount(() => {
  if (buildPollTimer) window.clearTimeout(buildPollTimer)
})

async function toggleEnabled() {
  if (!selected.value) return
  selected.value = selected.value.status === 'disabled'
    ? await algorithmsApi.enable(selected.value.id)
    : await algorithmsApi.disable(selected.value.id)
  await refresh()
}
async function rollbackVersion() {
  if (!selected.value || !window.confirm(`确认回滚到 ${selected.value.version}？`)) return
  await algorithmsApi.rollback(selected.value.id); await refresh()
}
async function deleteVersion() {
  if (!selected.value || !window.confirm(`确认删除版本 ${selected.value.version}？已有任务引用时会被拒绝。`)) return
  await algorithmsApi.remove(selected.value.id)
  selected.value = null; await refresh()
}
</script>

<template>
  <header class="page-heading">
    <span class="eyebrow">ALGORITHM REGISTRY</span>
    <h1>算法中心</h1>
    <p>上传算法包后，平台会自动构建、验收并发布，全程无需进入服务器。</p>
  </header>
  <p v-if="error || actionError" class="alert">{{ error || actionError }}</p>
  <p v-if="buildNotice" class="build-notice">{{ buildNotice }}</p>
  <div class="toolbar">
    <span>{{ algorithms.length }} 个算法版本</span>
    <div class="toolbar-actions">
      <button class="secondary-button" @click="refresh">刷新</button>
      <select v-if="canBuild" v-model="templateType" class="template-select" aria-label="算法模板类型">
        <option value="object_detection">目标检测模板</option>
        <option value="classification">图像分类模板</option>
        <option value="segmentation">图像分割模板</option>
        <option value="ocr">OCR 模板</option>
        <option value="pose_estimation">姿态估计模板</option>
      </select>
      <a v-if="canBuild" class="secondary-button template-download" :href="templateDownloadUrl" download>下载模板</a>
      <button
        v-if="canBuild"
        class="secondary-button"
        :aria-expanded="showImportGuide"
        @click="showImportGuide = !showImportGuide"
      >{{ showImportGuide ? '收起规范' : '查看规范' }}</button>
      <label v-if="canBuild" class="primary-button upload-button">
        {{ importing ? '上传并提交构建…' : '上传并自动构建' }}
        <input type="file" accept="application/zip,.zip" :disabled="importing" @change="importPackage" />
      </label>
    </div>
  </div>
  <section v-if="canBuild && showImportGuide" class="import-guide">
    <header>
      <div><span>IMPORT GUIDE</span><h2>算法包接入规范</h2></div>
      <p>下载最接近任务类型的模板，替换推理逻辑和示例图片后再导入。</p>
    </header>
    <div class="import-guide-grid">
      <article>
        <strong>ZIP 根目录结构</strong>
        <pre>manifest.yaml
service.py
algorithm.py
requirements.txt
README.md
test/sample.jpg
weights/</pre>
      </article>
      <article>
        <strong>导入流程</strong>
        <ol>
          <li>修改 manifest 的算法 ID、版本、运行环境和动态参数。</li>
          <li>在 algorithm.py 中加载模型并返回对应的 SDK Result。</li>
          <li>用真实图片替换 test/sample.jpg，将根目录内容压缩为 ZIP。</li>
          <li>上传 ZIP，平台自动构建镜像、验收三个标准接口并发布。</li>
        </ol>
        <p class="guide-warning">不要放入 Dockerfile、绝对路径、符号链接或设备文件。</p>
      </article>
      <article>
        <strong>平台协议</strong>
        <code>GET /health</code>
        <code>GET /metadata</code>
        <code>POST /predict</code>
        <p>service.py 已通过平台 SDK 实现协议，通常只需修改 algorithm.py。</p>
      </article>
    </div>
  </section>
  <div class="algorithm-filters">
    <input v-model="query" placeholder="搜索名称或算法 ID" />
    <select v-model="taskType"><option value="">全部任务类型</option><option value="object_detection">目标检测</option><option value="classification">分类</option><option value="segmentation">分割</option><option value="ocr">OCR</option><option value="pose_estimation">姿态</option></select>
    <select v-model="algorithmStatus"><option value="">全部状态</option><option value="available">可用</option><option value="disabled">停用</option><option value="uploaded">已上传</option><option value="building">构建中</option><option value="failed">失败</option></select>
  </div>
  <div v-if="loading" class="empty-state">正在加载…</div>
  <div v-else class="algorithm-grid">
    <AlgorithmCard
      v-for="item in filteredAlgorithms"
      :key="item.id"
      :algorithm="item"
      :selected="selected?.id === item.id"
      @select="selected = $event"
    />
  </div>
  <section v-if="selected" class="algorithm-detail section-block">
    <header class="section-heading">
      <div><span>VERSION</span><h2>{{ selected.name }} · {{ selected.version }}</h2></div>
    </header>
    <dl>
      <div><dt>镜像</dt><dd class="mono">{{ selected.image }}</dd></div>
      <div><dt>包 SHA-256</dt><dd class="mono">{{ selected.package_sha256 ?? '内置算法' }}</dd></div>
      <div><dt>镜像摘要</dt><dd class="mono">{{ selected.image_digest ?? '尚未构建' }}</dd></div>
      <div><dt>创建者</dt><dd>{{ selected.created_by }}</dd></div>
    </dl>
    <div class="runtime-actions">
      <button
        v-if="canBuild && ['uploaded', 'failed'].includes(selected.status)"
        class="primary-button compact-button"
        @click="buildAlgorithm"
      >{{ selected.status === 'failed' ? '重新构建' : '构建并发布' }}</button>
      <button
        v-if="isAdmin && ['available', 'disabled'].includes(selected.status)"
        class="secondary-button"
        @click="toggleEnabled"
      >{{ selected.status === 'disabled' ? '启用版本' : '停用版本' }}</button>
      <button v-if="isAdmin && ['available', 'disabled'].includes(selected.status)" class="secondary-button" @click="rollbackVersion">回滚到此版本</button>
      <button v-if="isAdmin" class="secondary-button danger-button" @click="deleteVersion">删除版本</button>
    </div>
    <div v-if="buildJob" class="build-log">
      <header><strong>构建日志</strong><span>{{ buildStatusLabel[buildJob.status] }}</span></header>
      <div class="build-progress">
        <progress :value="buildProgress[buildJob.status]" max="100" />
        <span>{{ buildProgress[buildJob.status] }}%</span>
      </div>
      <pre>{{ buildJob.logs.join('\n') || '等待构建 Worker…' }}</pre>
    </div>
  </section>
</template>
