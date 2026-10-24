<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { assetsApi } from '@/api/assets'
import { inferenceApi, type InferenceFrame, type InferenceSession } from '@/api/inference'
import { errorMessage } from '@/utils/errors'
import ReviewCollectButton from '@/components/training/ReviewCollectButton.vue'
import ResultViewer from './ResultViewer.vue'
const props = defineProps<{ session: InferenceSession }>()
const frames = ref<InferenceFrame[]>([])
const offset = ref(0)
const selectedIndex = ref<number | null>(null)
const follow = ref(true)
const trails = ref(true)
const original = ref(false)
const error = ref('')
const selected = computed(() => frames.value.find(f => f.index === selectedIndex.value) ?? frames.value[frames.value.length - 1])
let version = 0
async function load() {
  const request = ++version
  try {
    if (follow.value) offset.value = Math.max(0, props.session.processed - 20)
    const page = await inferenceApi.frames(props.session.id, offset.value)
    if (request !== version) return
    frames.value = page.items
    if (follow.value) selectedIndex.value = frames.value[frames.value.length - 1]?.index ?? null
    error.value = ''
  } catch (e) { error.value = errorMessage(e, '读取帧结果失败') }
}
watch(() => [props.session.id, props.session.processed], () => { void load() }, { immediate: true })
async function changePage(step: number) { follow.value = false; selectedIndex.value = null; offset.value = Math.max(0, offset.value + step * 20); await load() }
function goFrame(event: Event) { follow.value = false; const index = Number((event.target as HTMLInputElement).value); selectedIndex.value = index; offset.value = Math.floor(index/20)*20; void load() }
</script>
<template>
  <div class="inference-results">
    <p v-if="error || session.error" class="alert">{{ error || session.error }}</p>
    <p v-if="!session.processed" class="muted-copy">{{ session.status === 'queued' ? '等待执行槽；最多同时处理 2 个会话。' : '等待首帧结果，首次加载模型可能需要一些时间。' }}</p>
    <template v-if="session.processed">
      <div class="inference-result-tools"><label class="check-control"><input v-model="follow" type="checkbox" @change="load" />跟随最新结果</label><label class="check-control"><input v-model="original" type="checkbox" />查看原图</label><label v-if="session.config.tracking" class="check-control"><input v-model="trails" type="checkbox" />显示轨迹与 ID</label><a v-if="selected" class="text-button" :href="inferenceApi.file(session.id,`result-${String(selected.index).padStart(8,'0')}.png`)" download>下载本帧结果图</a></div>
      <label v-if="session.kind !== 'images'">帧时间轴（已处理 {{ session.processed }} 帧）<input class="inference-timeline" type="range" min="0" :max="session.processed - 1" :value="selected?.index ?? 0" @input="goFrame" /></label>
      <div class="inference-frame-strip"><button v-for="frame in frames" :key="frame.index" class="secondary-button" :class="{ selected: selected?.index === frame.index }" @click="selectedIndex = frame.index; follow = false">{{ session.kind === 'images' ? frame.input_name : `#${frame.source_index} · ${((frame.timestamp_ms ?? 0)/1000).toFixed(2)}s` }}<small>{{ frame.status === 'completed' ? '完成' : '失败/取消' }}</small></button></div>
      <div class="inference-pagination"><button class="text-button" :disabled="offset === 0" @click="changePage(-1)">前 20 帧</button><span>{{ offset + 1 }}–{{ Math.min(offset + 20, session.processed) }} / {{ session.processed }}</span><button class="text-button" :disabled="offset + 20 >= session.processed" @click="changePage(1)">后 20 帧</button></div>
      <template v-if="selected"><p class="muted-copy">{{ selected.input_name }} · 片段 {{ selected.segment }}<template v-if="selected.timestamp_ms !== null"> · 源时间 {{ (selected.timestamp_ms/1000).toFixed(3) }} 秒</template> · <RouterLink :to="`/tasks/${selected.task_id}`">原始任务详情</RouterLink></p>
        <ReviewCollectButton v-if="selected.result?.type === 'object_detection'" :key="selected.task_id" :task-id="selected.task_id" />
        <p v-if="selected.error" class="alert">{{ selected.error }}</p>
        <img v-if="original" class="inference-preview" :src="assetsApi.contentUrl(selected.asset_id)" :alt="selected.input_name" />
        <template v-else-if="trails && session.config.tracking && selected.result"><img class="inference-preview" :src="inferenceApi.file(session.id,`result-${String(selected.index).padStart(8,'0')}.jpg`)" alt="目标 ID 与轨迹" /><div class="inference-track-list"><span v-for="track in selected.tracks" :key="track.track_id" class="count-badge">片段 {{ selected.segment }} / #{{ track.track_id }} · {{ track.label }} · {{ (track.score*100).toFixed(1) }}%</span></div><p class="muted-copy">轨迹 ID 仅在此片段内有效，不代表真实身份。</p></template>
        <ResultViewer v-else-if="selected.result" :result="selected.result" :image-url="assetsApi.contentUrl(selected.asset_id)" />
      </template>
      <details v-if="session.artifacts['result.mp4']"><summary>查看标注结果视频</summary><video class="inference-preview" :src="inferenceApi.file(session.id,'result.mp4')" controls preload="metadata" /></details>
    </template>
  </div>
</template>
