<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { inferenceApi, inferenceStatus, inputLabels, terminal, type InferenceSession } from '@/api/inference'
import { errorMessage } from '@/utils/errors'
const items = ref<InferenceSession[]>([])
const search = ref('')
const status = ref('all')
const offset = ref(0)
const error = ref('')
const visible = computed(() => items.value.filter(s => (!search.value || `${s.name} ${s.model.name}`.toLowerCase().includes(search.value.toLowerCase())) && (status.value === 'all' || (status.value === 'active' ? !terminal(s.status) : terminal(s.status)))))
let timer: ReturnType<typeof setInterval> | undefined
let loading = false
async function load() { if (loading) return; loading = true; try { items.value = await inferenceApi.list(offset.value); error.value = '' } catch (e) { error.value = errorMessage(e, '读取推理任务失败') } finally { loading = false } }
async function stop(s: InferenceSession) { try { await inferenceApi.stop(s.id); await load() } catch (e) { error.value = errorMessage(e, '停止失败') } }
function page(step: number) { offset.value = Math.max(0, offset.value + step * 30); void load() }
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load() }, 3000) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <header class="page-heading page-heading-actions"><div><h1>推理任务</h1><p>图片批次、视频和实时采集会话。服务器端任务离开页面后继续运行。</p></div><RouterLink to="/" class="primary-button">新建推理</RouterLink></header>
  <div class="inference-list-tools"><input v-model="search" type="search" placeholder="筛选本页任务或模型" aria-label="搜索推理会话" /><select v-model="status" aria-label="会话状态"><option value="all">本页全部状态</option><option value="active">运行与排队</option><option value="finished">已结束</option></select><RouterLink class="secondary-button" to="/tasks">历史单图任务</RouterLink><RouterLink class="secondary-button" to="/advanced?tab=media">旧版媒体记录</RouterLink></div>
  <p v-if="error" class="alert">{{ error }}</p>
  <div v-if="!visible.length" class="empty-state">本页暂无匹配会话。</div>
  <section v-else class="section-block"><div v-for="item in visible" :key="item.id" class="inference-task-row"><RouterLink :to="`/inference/tasks/${item.id}`"><strong>{{ item.name }}</strong><small>{{ item.model.name }} · {{ inputLabels[item.kind] }} · {{ new Date(item.created_at).toLocaleString() }}</small></RouterLink><span>{{ inferenceStatus[item.status] }}</span><span>处理 {{ item.processed }}<small v-if="item.failed">失败 {{ item.failed }}</small></span><button v-if="!terminal(item.status)" class="text-button" @click="stop(item)">停止</button><RouterLink v-else class="text-button" :to="`/inference/tasks/${item.id}`">结果与报告</RouterLink></div></section>
  <div class="inference-pagination"><button class="secondary-button" :disabled="offset === 0" @click="page(-1)">上一页</button><span>第 {{ offset / 30 + 1 }} 页</span><button class="secondary-button" :disabled="items.length < 30" @click="page(1)">下一页</button></div>
</template>
