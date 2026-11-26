<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { evaluationAnalysisApi, type ErrorSample } from '@/api/evaluations'
import { assetsApi } from '@/api/assets'
import { errorMessage } from '@/utils/errors'
import ReviewCollectButton from './ReviewCollectButton.vue'
const props=defineProps<{runId:string;algorithms:Array<{id:string;name:string}>}>()
const algorithm=ref(''),kind=ref('all'),label=ref(''),labels=ref<string[]>([]),offset=ref(0),items=ref<ErrorSample[]>([]),total=ref(0),selected=ref<ErrorSample|null>(null),error=ref(''),loading=ref(false)
const dimensions=ref({width:1,height:1})
let sequence=0
async function load(){if(!algorithm.value)return;const n=++sequence;loading.value=true;try{const page=await evaluationAnalysisApi.samples(props.runId,algorithm.value,kind.value,label.value,offset.value);if(n!==sequence)return;items.value=page.items;total.value=page.total;labels.value=page.labels;selected.value=null;error.value=''}catch(e){if(n===sequence)error.value=errorMessage(e,'错误样本读取失败')}finally{if(n===sequence)loading.value=false}}
watch(()=>props.algorithms,()=>{if(!algorithm.value&&props.algorithms[0])algorithm.value=props.algorithms[0].id},{immediate:true})
watch([algorithm,kind,label,()=>props.runId],()=>{offset.value=0;void load()},{immediate:true})
async function choose(sample:ErrorSample){selected.value=sample;const asset=await assetsApi.get(sample.asset_id);if(selected.value?.task_id===sample.task_id)dimensions.value={width:asset.width,height:asset.height}}
async function page(step:number){offset.value+=step*24;await load()}
const legend=computed(()=>selected.value?`正确 ${selected.value.tp} · 误检 ${selected.value.fp} · 漏检 ${selected.value.fn}`:'')
</script>
<template><section class="section-block"><header class="section-heading"><h2>错误样本分析</h2><button class="text-button" :disabled="loading" @click="load">刷新</button></header><p class="muted-copy">固定 IoU ≥ 0.5、同类别、按预测置信度从高到低一对一匹配。仅分析成功完成的检测结果；错分类会同时计入误检和漏检。这里的指标不是 mAP。</p>
  <div class="training-list-filters"><label>模型<select v-model="algorithm" aria-label="错误样本模型"><option v-for="a in algorithms" :key="a.id" :value="a.id">{{ a.name }}</option></select></label><label>筛选<select v-model="kind" aria-label="错误类型"><option value="all">全部样本</option><option value="fp">包含误检</option><option value="fn">包含漏检</option><option value="tp">包含正确检测</option></select></label><label>类别<select v-model="label" aria-label="错误类别"><option value="">全部类别</option><option v-for="name in labels" :key="name">{{ name }}</option></select></label></div>
  <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="loading" class="muted-copy">正在分析…</p><p v-else-if="!items.length" class="empty-state">暂无符合条件的已完成样本。</p>
  <div class="error-samples"><button v-for="sample in items" :key="sample.task_id" class="error-sample" @click="choose(sample).catch(e=>error=errorMessage(e,'原图读取失败'))"><img :src="assetsApi.thumbnailUrl(sample.asset_id,320)" alt="评测样本" /><span>正确 {{ sample.tp }} · 误检 {{ sample.fp }} · 漏检 {{ sample.fn }}</span></button></div>
  <div class="training-pagination"><span>共 {{ total }} 张</span><button class="text-button" :disabled="offset===0||loading" @click="page(-1)">上一页</button><button class="text-button" :disabled="offset+24>=total||loading" @click="page(1)">下一页</button></div>
  <div v-if="selected" class="sample-inspector"><div class="toolbar-actions"><strong>{{ legend }}</strong><ReviewCollectButton :key="selected.task_id" :task-id="selected.task_id" :evaluation-id="runId" /></div><p class="muted-copy">实线：预测框（青色正确、红色误检）；虚线：真实标注（绿色已匹配、橙色漏检）。类别筛选作用于对应错误框，打开图片后展示完整上下文。</p>
    <svg :viewBox="`0 0 ${dimensions.width} ${dimensions.height}`" role="img" aria-label="预测与真实标注对照"><image :href="assetsApi.contentUrl(selected.asset_id)" :width="dimensions.width" :height="dimensions.height" /><g v-for="(box,i) in selected.predictions" :key="`p${i}`"><rect :x="box.bbox[0]" :y="box.bbox[1]" :width="box.bbox[2]-box.bbox[0]" :height="box.bbox[3]-box.bbox[1]" :stroke="selected.false_positives.includes(i)?'#ef4444':'#22d3ee'" fill="transparent" stroke-width="2" vector-effect="non-scaling-stroke" /><text :x="box.bbox[0]" :y="Math.max(18,box.bbox[1]-4)" fill="white" stroke="black" stroke-width=".4" :font-size="Math.max(14,dimensions.width/70)">预测 {{ box.label }}</text></g><g v-for="(box,i) in selected.ground_truth" :key="`t${i}`"><rect :x="box.bbox[0]" :y="box.bbox[1]" :width="box.bbox[2]-box.bbox[0]" :height="box.bbox[3]-box.bbox[1]" :stroke="selected.false_negatives.includes(i)?'#f59e0b':'#22c55e'" fill="transparent" stroke-width="2" stroke-dasharray="6 4" vector-effect="non-scaling-stroke" /><text :x="box.bbox[0]" :y="Math.min(dimensions.height-5,box.bbox[3]+18)" fill="#a7f3d0" stroke="black" stroke-width=".4" :font-size="Math.max(14,dimensions.width/70)">标注 {{ box.label }}</text></g></svg>
  </div>
</section></template>
<style scoped>
.error-samples{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}.error-sample{padding:0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--surface);color:var(--text)}.error-sample img{width:100%;height:120px;object-fit:cover}.error-sample span{display:block;padding:8px;font-size:12px}.sample-inspector{margin-top:20px}.sample-inspector svg{width:100%;max-height:65vh;background:#152033}
</style>
