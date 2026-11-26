<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { projectContext } from '@/api/http'
import { trainingBatchesApi, type BatchContent, type BatchJob, type BatchRequest, type DeleteCheck } from '@/api/trainingBatches'
import { errorMessage } from '@/utils/errors'

const emit = defineEmits<{ removed: [ids: string[]] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const action = ref<'delete' | 'download'>('download')
const targets = ref<string[]>([])
const checks = ref<DeleteCheck[]>([])
const checking = ref(false)
const submitting = ref(false)
const modalError = ref('')
const pollError = ref('')
const content = ref<BatchContent>('all')
const job = ref<BatchJob | null>(null)
const pending = ref<BatchRequest | null>(null)
const active = computed(() => job.value?.status === 'queued' || job.value?.status === 'running')
const eligible = computed(() => checks.value.filter(item => item.eligible).length)
const failedItems = computed(() => job.value?.items.filter(item => item.status !== 'success') ?? [])
const succeeded = computed(() => job.value?.items.filter(item => item.status === 'success').length ?? 0)
const skipped = computed(() => job.value?.items.filter(item => item.status === 'skipped').length ?? 0)
const failed = computed(() => job.value?.items.filter(item => item.status === 'failed').length ?? 0)
const retryIds = computed(() => {
  if (!job.value) return []
  if (job.value.request.action === 'download' && job.value.status === 'failed' && !job.value.download_ready) return job.value.request.run_ids
  return job.value.request.run_ids.filter(id => !job.value?.items.some(item => item.id === id && item.status === 'success'))
})
const storageKey = `cv-training-batch:${projectContext.get() ?? 'default'}`
let timer: number | undefined
let disposed = false
let previewSequence = 0
let origin: HTMLElement | null = null
const contentLabels: Record<BatchContent,string> = { weights:'模型权重', reports:'训练报告（PDF + JSON）', metrics:'指标 CSV', all:'完整结果' }
function close() { previewSequence++; dialog.value?.close(); origin?.focus() }
async function open(ids: string[], operation: 'delete' | 'download') {
  if (active.value || submitting.value || !ids.length) return
  origin = document.activeElement as HTMLElement
  action.value = operation; targets.value = [...ids]; checks.value = []; modalError.value = ''; pending.value = null
  checking.value = operation === 'delete'
  await nextTick(); dialog.value?.showModal()
  if (operation === 'delete') await preview()
}
async function preview() {
  const sequence = ++previewSequence
  checking.value = true; modalError.value = ''
  try {
    const response = await trainingBatchesApi.preview(targets.value)
    if (sequence === previewSequence) checks.value = response.items
  } catch (reason) { if (sequence === previewSequence) modalError.value = errorMessage(reason, '检查失败，请重试') }
  finally { if (sequence === previewSequence) checking.value = false }
}
function newRequestId(): string {
  // getRandomValues is available on LAN HTTP as well as HTTPS.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-')
}
async function submit() {
  if (submitting.value || checking.value || (action.value === 'delete' && !eligible.value)) return
  submitting.value = true; modalError.value = ''
  try {
    pending.value ??= {request_id:newRequestId(),run_ids:[...targets.value],action:action.value,content:content.value}
    localStorage.setItem(storageKey, pending.value.request_id)
    job.value = await trainingBatchesApi.create(pending.value)
    close(); pollError.value = ''; await poll()
  } catch (reason) { modalError.value = errorMessage(reason, '提交失败，请重试；重试不会重复执行') }
  finally { submitting.value = false }
}
async function poll() {
  window.clearTimeout(timer)
  const id = job.value?.id ?? localStorage.getItem(storageKey)
  if (!id || disposed) return
  try {
    const response = await trainingBatchesApi.get(id)
    if (disposed) return
    job.value = response; pollError.value = ''
    if (response.request.action === 'delete') emit('removed', response.items.filter(item => item.status === 'success').map(item => item.id))
    if (active.value) timer = window.setTimeout(poll, 1500)
  } catch (reason) { if (!disposed) pollError.value = errorMessage(reason, '进度读取失败，请重新读取') }
}
function retry() {
  if (!job.value || !retryIds.value.length) return
  content.value = job.value.request.content
  void open(retryIds.value, job.value.request.action)
}
function dismiss() { job.value = null; pollError.value = ''; localStorage.removeItem(storageKey) }
onMounted(() => { if (localStorage.getItem(storageKey)) void poll() })
onBeforeUnmount(() => { disposed = true; previewSequence++; window.clearTimeout(timer) })
defineExpose({open, active})
</script>

<template>
  <section v-if="job || pollError" class="batch-result" aria-label="批量操作进度">
    <header><strong>{{ job?.request.action === 'delete' ? '批量删除' : '批量下载' }}<template v-if="job"> · {{ active ? '处理中' : job.status === 'failed' ? '操作中断' : '处理完成' }}</template></strong><button v-if="!active" type="button" class="text-button" @click="dismiss">关闭</button></header>
    <template v-if="job">
      <p role="status">已处理 {{ job.processed }} / {{ job.total }} 项 · 成功 {{ succeeded }} · 失败 {{ failed }} · 跳过 {{ skipped }}</p>
      <progress v-if="active" :value="job.processed" :max="job.total" aria-label="批量操作进度" />
      <p v-if="job.current" class="muted-copy">正在处理：{{ job.current }}</p>
      <p v-if="job.error" class="alert" role="alert">{{ job.error }}</p>
      <div class="toolbar-actions"><a v-if="job.download_ready" class="primary-button compact-button" :href="trainingBatchesApi.download(job.id)" download>下载 ZIP</a><button v-if="!active && retryIds.length" class="secondary-button compact-button" type="button" @click="retry">重试未完成的 {{ retryIds.length }} 项</button></div>
      <p v-if="job.download_ready" class="muted-copy">下载包保留 24 小时，包含逐项操作结果。运行中任务的结果仅为当前快照。</p>
      <details v-if="failedItems.length" class="batch-failures" open><summary>失败与跳过原因（{{ failedItems.length }}）</summary><ul><li v-for="item in failedItems" :key="item.id"><strong>{{ item.name }}</strong><span>{{ item.reason }}</span></li></ul></details>
    </template>
    <p v-if="pollError" class="alert" role="alert">{{ pollError }} <button type="button" class="text-button" @click="poll">重新读取</button></p>
  </section>
  <dialog ref="dialog" class="batch-dialog" aria-labelledby="batch-dialog-title" @cancel.prevent="!submitting && close()">
    <h2 id="batch-dialog-title">{{ action === 'delete' ? '确认删除任务' : '下载训练结果' }}（{{ targets.length }} 项）</h2>
    <template v-if="action === 'delete'">
      <p>将永久删除任务记录、权重、日志和结果文件。数据集和已独立发布的模型会保留。</p>
      <p v-if="checking" role="status">正在检查任务状态和引用关系…</p>
      <template v-else-if="checks.length"><p>可删除 {{ eligible }} 项，跳过 {{ checks.length - eligible }} 项。执行时会再次检查状态。</p><ul class="batch-checks"><li v-for="item in checks" :key="item.id"><strong>{{ item.name }}</strong><span>{{ item.eligible ? '可删除' : item.reason }}</span></li></ul></template>
    </template>
    <template v-else>
      <p>打包为一个 ZIP，每个任务独立存放。</p>
      <fieldset class="batch-content"><legend>下载内容</legend><label v-for="(label, key) in contentLabels" :key="key"><input v-model="content" type="radio" name="batch-content" :value="key" :disabled="submitting || Boolean(pending)" />{{ label }}</label></fieldset>
    </template>
    <p v-if="modalError" class="alert" role="alert">{{ modalError }} <button v-if="action === 'delete' && !checks.length" class="text-button" @click="preview">重新检查</button></p>
    <footer><button type="button" class="secondary-button" :disabled="submitting" autofocus @click="close">取消</button><button type="button" :class="action === 'delete' ? 'danger-button' : 'primary-button'" :disabled="submitting || checking || (action === 'delete' && !eligible)" @click="submit">{{ submitting ? '正在提交…' : pending ? '重试提交' : action === 'delete' ? `删除 ${eligible} 项` : '开始打包' }}</button></footer>
  </dialog>
</template>
<style scoped>
.batch-result { border:1px solid var(--line); border-radius:10px; padding:18px; margin-bottom:18px; background:var(--surface-2); }
.batch-result header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.batch-result p { font-size:13px; overflow-wrap:anywhere; }.batch-result progress { width:100%; height:9px; accent-color:var(--accent); }
.batch-dialog { width:min(580px,calc(100vw - 32px)); max-height:85vh; overflow:auto; border:1px solid var(--line); border-radius:12px; padding:24px; background:var(--surface); color:var(--text); box-shadow:var(--shadow-lg); }
.batch-dialog::backdrop { background:var(--overlay); }.batch-dialog h2 { margin:0 0 16px; font-size:19px; }.batch-dialog p { font-size:14px; line-height:1.7; }
.batch-dialog footer { display:flex; gap:12px; justify-content:flex-end; margin-top:24px; }.batch-dialog footer button { min-height:42px; }.danger-button { background:var(--danger); color:white; border:1px solid var(--danger); border-radius:8px; padding:10px 16px; cursor:pointer; }.danger-button:disabled { opacity:.5; cursor:not-allowed; }
.batch-content { border:0; padding:0; display:grid; gap:12px; }.batch-content legend { margin-bottom:12px; font-weight:600; }.batch-content label { display:flex; align-items:center; gap:10px; min-height:40px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; cursor:pointer; }.batch-content input { width:16px; height:16px; }
.batch-checks,.batch-failures ul { list-style:none; padding:0; max-height:250px; overflow:auto; }.batch-checks li,.batch-failures li { display:grid; gap:4px; padding:10px 0; border-bottom:1px solid var(--line); font-size:13px; overflow-wrap:anywhere; }.batch-checks span,.batch-failures span { color:var(--muted); }.batch-failures { margin-top:16px; }
</style>
