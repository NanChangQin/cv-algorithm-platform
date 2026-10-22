<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { algorithmsApi } from '@/api/algorithms'
import type { AlgorithmTemplateType } from '@/api/algorithms'
import { sessionRole } from '@/api/auth'
import { apiUrl } from '@/api/http'
import AlgorithmCard from '@/components/algorithms/AlgorithmCard.vue'
import UploadDropzone from '@/components/assets/UploadDropzone.vue'
import { useAlgorithms } from '@/composables/useAlgorithms'
import { toast } from '@/composables/useToast'
import type { Algorithm, BuildJob } from '@/types/algorithm'
import { errorMessage } from '@/utils/errors'
import { parameterRange, parameterTypeLabel } from '@/utils/parameters'
import { algorithmStatusLabel, formatDateTime, runtimeStatusLabel, taskTypeLabel } from '@/utils/presentation'
import { buildProgress, buildStatusLabel } from '@/utils/status'

const { algorithms, availableAlgorithms, loading, error, refresh } = useAlgorithms()
const actionError = ref('')
const buildNotice = ref('')
const importing = ref(false)
const acting = ref(false)
const selectedId = ref('')
const buildJob = ref<BuildJob | null>(null)
const query = ref('')
const taskType = ref('')
const algorithmStatus = ref('')
const templateType = ref<AlgorithmTemplateType>('object_detection')
const showImportGuide = ref(false)
const detailPanel = ref<HTMLElement | null>(null)
const templateDownloadUrl = computed(() => apiUrl(`/algorithms/template?task_type=${encodeURIComponent(templateType.value)}`))
const filteredAlgorithms = computed(() => algorithms.value.filter((algorithm) =>
  (!query.value || algorithm.name.toLowerCase().includes(query.value.toLowerCase()) || algorithm.key.includes(query.value.toLowerCase()))
  && (!taskType.value || algorithm.task_type === taskType.value)
  && (!algorithmStatus.value || algorithm.status === algorithmStatus.value),
))
const selected = computed(() => algorithms.value.find((item) => item.id === selectedId.value) ?? null)
const siblings = computed(() => selected.value ? algorithms.value.filter((item) => item.key === selected.value!.key && item.id !== selected.value!.id) : [])
const canBuild = computed(() => ['admin', 'developer'].includes(sessionRole.get() ?? 'user'))
const isAdmin = computed(() => sessionRole.get() === 'admin')
let buildPollTimer: number | undefined

async function select(algorithm: Algorithm) {
  selectedId.value = algorithm.id
  if (buildJob.value && buildJob.value.algorithm_version_id !== algorithm.id) buildJob.value = null
  await nextTick()
  // 窄屏时详情在列表下方，自动滚过去
  if (window.matchMedia?.('(max-width: 1180px)').matches) detailPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function importPackage(files: File[]) {
  const file = files[0]
  if (!file) return
  importing.value = true
  actionError.value = ''
  buildNotice.value = ''
  buildJob.value = null
  try {
    const imported = await algorithmsApi.importPackage(file)
    await refresh()
    await select(imported)
    buildNotice.value = `${imported.name} 已上传，正在提交自动构建…`
    await startBuild(imported)
  } catch (reason) {
    actionError.value = errorMessage(reason, '上传或提交自动构建失败')
    buildNotice.value = ''
  } finally {
    importing.value = false
  }
}

async function buildAlgorithm() {
  if (!selected.value) return
  actionError.value = ''
  buildNotice.value = ''
  try {
    await startBuild(selected.value)
  } catch (reason) {
    actionError.value = errorMessage(reason, '提交构建失败')
  }
}

async function startBuild(algorithm: Algorithm) {
  if (buildPollTimer) window.clearTimeout(buildPollTimer)
  buildJob.value = await algorithmsApi.build(algorithm.id)
  buildNotice.value = `${algorithm.name} 正在后台构建并验收，请勿重复上传。`
  await refresh()
  pollBuild(buildJob.value.id)
}

function pollBuild(jobId: string) {
  buildPollTimer = window.setTimeout(async () => {
    try {
      const job = await algorithmsApi.buildJob(jobId)
      if (buildJob.value?.id !== jobId) return
      buildJob.value = job
      actionError.value = ''
      await refresh()
      if (job.status === 'completed') {
        buildNotice.value = ''
        toast.success(`${selected.value?.name ?? '算法'} 构建和协议验收通过，已发布为可用版本`)
        return
      }
      if (job.status === 'failed') {
        buildNotice.value = ''
        actionError.value = job.error_message ?? '算法构建失败，请查看下方构建日志后重新构建。'
        return
      }
      pollBuild(jobId)
    } catch (reason) {
      actionError.value = `${errorMessage(reason, '获取构建进度失败')}；后台构建不受影响，正在重试获取进度。`
      pollBuild(jobId)
    }
  }, 1500)
}

onBeforeUnmount(() => {
  if (buildPollTimer) window.clearTimeout(buildPollTimer)
})

/** 版本管理操作统一处理：失败要告诉用户原因（例如版本已被任务引用不能删除） */
async function run(action: () => Promise<unknown>, success: string, fallback: string) {
  if (acting.value) return
  acting.value = true
  actionError.value = ''
  try {
    await action()
    await refresh()
    toast.success(success)
  } catch (reason) {
    actionError.value = errorMessage(reason, fallback)
  } finally { acting.value = false }
}
function toggleEnabled() {
  const target = selected.value
  if (!target) return
  const enabling = target.status === 'disabled'
  return run(() => enabling ? algorithmsApi.enable(target.id) : algorithmsApi.disable(target.id), enabling ? `已启用 ${target.name} v${target.version}` : `已停用 ${target.name} v${target.version}`, '切换版本状态失败')
}
function rollbackVersion() {
  const target = selected.value
  if (!target || !window.confirm(`确认把 ${target.name} 回滚到 v${target.version}？同一算法的其他可用版本会被停用。`)) return
  return run(() => algorithmsApi.rollback(target.id), `已回滚到 v${target.version}`, '回滚失败')
}
function deleteVersion() {
  const target = selected.value
  if (!target || !window.confirm(`确认删除 ${target.name} v${target.version}？已有任务引用的版本不能删除。`)) return
  return run(async () => { await algorithmsApi.remove(target.id); selectedId.value = '' }, `已删除 ${target.name} v${target.version}`, '删除版本失败')
}
async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); toast.success('已复制') } catch { toast.info(text) }
}
</script>

<template>
  <header class="page-heading page-heading-actions">
    <div><h1>算法库</h1><p>上传算法包后，平台会自动构建、验收并发布，全程无需进入服务器。</p></div>
    <div class="toolbar-actions">
      <button class="secondary-button" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
      <RouterLink v-if="availableAlgorithms.length > 1" class="secondary-button" to="/compare">多算法对比</RouterLink>
    </div>
  </header>
  <p v-if="error || actionError" class="alert" role="alert">{{ error || actionError }}</p>
  <p v-if="buildNotice" class="build-notice" role="status">{{ buildNotice }}</p>

  <section v-if="canBuild" class="import-panel">
    <UploadDropzone accept="application/zip,.zip" :multiple="false" :disabled="importing" compact :title="importing ? '正在上传并提交构建…' : '拖入算法包 ZIP，或点击选择'" hint="上传后自动构建镜像、验收三个标准接口并发布" @files="importPackage" />
    <div class="import-panel-actions">
      <label class="field-inline">模板类型
        <select v-model="templateType" class="template-select" aria-label="算法模板类型">
          <option value="object_detection">目标检测</option>
          <option value="classification">图像分类</option>
          <option value="segmentation">图像分割</option>
          <option value="ocr">文字识别</option>
          <option value="pose_estimation">姿态估计</option>
        </select>
      </label>
      <a class="secondary-button template-download" :href="templateDownloadUrl" download>下载模板</a>
      <button class="text-button" :aria-expanded="showImportGuide" @click="showImportGuide = !showImportGuide">{{ showImportGuide ? '收起接入规范' : '查看接入规范' }}</button>
    </div>
  </section>
  <section v-if="canBuild && showImportGuide" class="import-guide">
    <header>
      <div><h2>算法包接入规范</h2></div>
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
    <input v-model="query" type="search" placeholder="搜索名称或算法 ID" aria-label="搜索算法" />
    <select v-model="taskType" aria-label="按任务类型筛选"><option value="">全部任务类型</option><option v-for="(label, value) in taskTypeLabel" :key="value" :value="value">{{ label }}</option></select>
    <select v-model="algorithmStatus" aria-label="按状态筛选"><option value="">全部状态</option><option v-for="(label, value) in algorithmStatusLabel" :key="value" :value="value">{{ label }}</option></select>
  </div>
  <div class="algorithm-workbench" :class="{ 'has-detail': selected }">
    <div>
      <div v-if="loading && !algorithms.length" class="empty-state">正在加载…</div>
      <div v-else-if="!filteredAlgorithms.length" class="empty-state"><strong>{{ algorithms.length ? '没有匹配的算法' : '还没有算法' }}</strong><p>{{ algorithms.length ? '尝试修改筛选条件。' : '下载模板，按规范实现推理逻辑后上传算法包。' }}</p></div>
      <div v-else class="algorithm-grid">
        <AlgorithmCard v-for="item in filteredAlgorithms" :key="item.id" :algorithm="item" :selected="selected?.id === item.id" @select="select" />
      </div>
    </div>
    <section v-if="selected" ref="detailPanel" class="algorithm-detail section-block" aria-label="算法版本详情">
      <header class="section-heading">
        <div><h2>{{ selected.name }}</h2><span class="count-badge">v{{ selected.version }}</span></div>
        <button type="button" class="icon-button" aria-label="关闭详情" @click="selectedId = ''">×</button>
      </header>
      <p v-if="selected.description" class="muted-copy">{{ selected.description }}</p>
      <div class="detail-chips">
        <span :class="['runtime-status', selected.status === 'available' ? 'status-healthy' : selected.status === 'failed' ? 'status-failed' : 'status-starting']">{{ algorithmStatusLabel[selected.status] ?? selected.status }}</span>
        <span class="runtime-status">{{ taskTypeLabel[selected.task_type] }}</span>
        <span class="runtime-status">{{ selected.framework }} · {{ selected.device.toUpperCase() }}</span>
        <span class="runtime-status">实例{{ runtimeStatusLabel[selected.container_status] ?? '未启动' }}</span>
      </div>
      <div class="runtime-actions">
        <RouterLink v-if="selected.status === 'available'" class="primary-button compact-button" :to="{ path: '/', query: { algorithm: selected.id } }">使用此算法 →</RouterLink>
        <RouterLink v-if="selected.status === 'available' && availableAlgorithms.length > 1" class="secondary-button" :to="{ path: '/compare', query: { algorithms: selected.id } }">与其他算法对比</RouterLink>
        <button v-if="canBuild && ['uploaded', 'failed'].includes(selected.status)" class="primary-button compact-button" :disabled="acting" @click="buildAlgorithm">{{ selected.status === 'failed' ? '重新构建' : '构建并发布' }}</button>
        <button v-if="isAdmin && ['available', 'disabled'].includes(selected.status)" class="secondary-button" :disabled="acting" @click="toggleEnabled">{{ selected.status === 'disabled' ? '启用版本' : '停用版本' }}</button>
        <button v-if="isAdmin && siblings.length && ['available', 'disabled'].includes(selected.status)" class="secondary-button" :disabled="acting" @click="rollbackVersion">回滚到此版本</button>
        <button v-if="isAdmin" class="secondary-button danger-button" :disabled="acting" @click="deleteVersion">删除版本</button>
      </div>

      <h3 class="detail-subheading">运行参数</h3>
      <p v-if="!Object.keys(selected.parameters).length" class="muted-copy">此算法没有可调参数。</p>
      <div v-else class="parameter-table">
        <div v-for="(spec, name) in selected.parameters" :key="name">
          <strong>{{ spec.title ?? name }}<small class="mono">{{ name }}</small></strong>
          <span>{{ parameterTypeLabel[spec.type] }} · {{ parameterRange(spec) }}</span>
          <p v-if="spec.description">{{ spec.description }}</p>
        </div>
      </div>

      <template v-if="siblings.length">
        <h3 class="detail-subheading">同一算法的其他版本</h3>
        <div class="version-list">
          <button v-for="item in siblings" :key="item.id" type="button" @click="select(item)">
            <span>v{{ item.version }}</span><small>{{ algorithmStatusLabel[item.status] ?? item.status }} · {{ formatDateTime(item.created_at) }}</small>
          </button>
        </div>
      </template>

      <h3 class="detail-subheading">版本信息</h3>
      <dl>
        <div><dt>算法 ID</dt><dd class="mono">{{ selected.key }}</dd></div>
        <div><dt>创建者</dt><dd>{{ selected.created_by }} · {{ formatDateTime(selected.created_at) }}</dd></div>
        <div><dt>最近调用</dt><dd>{{ formatDateTime(selected.last_called_at) }}</dd></div>
        <div><dt>镜像</dt><dd class="mono"><button type="button" class="text-button mono" title="复制" @click="copy(selected.image)">{{ selected.image }}</button></dd></div>
        <div><dt>包 SHA-256</dt><dd class="mono">{{ selected.package_sha256 ? `${selected.package_sha256.slice(0, 16)}…` : '内置算法' }}</dd></div>
        <div><dt>镜像摘要</dt><dd class="mono">{{ selected.image_digest ? `${selected.image_digest.slice(0, 19)}…` : '尚未构建' }}</dd></div>
      </dl>
      <div v-if="buildJob && buildJob.algorithm_version_id === selected.id" class="build-log">
        <header><strong>构建日志</strong><span>{{ buildStatusLabel[buildJob.status] }}</span></header>
        <div class="build-progress">
          <progress :value="buildProgress[buildJob.status]" max="100" />
          <span>{{ buildProgress[buildJob.status] }}%</span>
        </div>
        <pre>{{ buildJob.logs.join('\n') || '等待构建 Worker…' }}</pre>
      </div>
    </section>
  </div>
</template>
