<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { reviewsApi, type SampleReview } from '@/api/reviews'
import { trainingApi, type TrainingDataset } from '@/api/training'
import { assetsApi } from '@/api/assets'
import DetectionEditor from '@/components/training/DetectionEditor.vue'
import { errorMessage } from '@/utils/errors'
const route=useRoute(), items=ref<SampleReview[]>([]), total=ref(0), offset=ref(0), status=ref('all'), current=ref<SampleReview|null>(null)
const error=ref(''),busy=ref(false),dirty=ref(false),datasets=ref<TrainingDataset[]>([]),datasetId=ref(''),split=ref('train'),ack=ref(false),selected=ref<string[]>([]),created=ref<TrainingDataset|null>(null)
const labels: Record<string,string>={draft:'待审核',reviewed:'已审核',published:'已生成版本'}
const suggestions=computed(()=>[...new Set([...(datasets.value.find(d=>d.id===datasetId.value)?.class_names??[]),...(current.value?.boxes.map(b=>b.label)??[])])])
async function refresh(){try{const page=await reviewsApi.list(status.value,offset.value);items.value=page.items;total.value=page.total}catch(e){error.value=errorMessage(e,'样本读取失败')}}
async function choose(id:string){if(dirty.value&&!window.confirm('当前修改尚未保存，放弃修改并切换？'))return;try{current.value=await reviewsApi.get(id);dirty.value=false}catch(e){error.value=errorMessage(e,'读取失败')}}
async function filter(){offset.value=0;selected.value=[];await refresh()}
async function page(delta:number){offset.value+=delta*24;selected.value=[];await refresh()}
async function save(reviewed:boolean){if(!current.value)return;busy.value=true;error.value='';try{current.value=await reviewsApi.save(current.value,reviewed);dirty.value=false;await refresh()}catch(e){error.value=errorMessage(e,'保存失败')}finally{busy.value=false}}
async function publish(){if(!datasetId.value||!selected.value.length)return;busy.value=true;error.value='';try{created.value=await reviewsApi.publish(datasetId.value,selected.value,split.value,ack.value);datasets.value.unshift(created.value);selected.value=[];await refresh();if(current.value)await choose(current.value.id)}catch(e){error.value=errorMessage(e,'生成版本失败')}finally{busy.value=false}}
onMounted(async()=>{await refresh();try{datasets.value=await trainingApi.datasets();datasetId.value=datasets.value[0]?.id??'';if(typeof route.query.review==='string')await choose(route.query.review)}catch(e){error.value=errorMessage(e,'读取数据集失败')}})
onBeforeRouteLeave(()=>!dirty.value||window.confirm('当前修改尚未保存，确认离开？'))
</script>
<template>
  <header class="page-heading page-heading-actions"><div><h1>问题样本与标注审核</h1><p>收集问题图片，修正标注并人工审核，然后生成新的训练数据版本。</p></div><RouterLink class="secondary-button" to="/evaluations">返回评测中心</RouterLink></header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p>
  <div class="review-layout"><section class="section-block"><header class="section-heading"><h2>样本队列 · {{ total }}</h2><select v-model="status" aria-label="审核状态" @change="filter"><option value="all">全部状态</option><option v-for="(label,key) in labels" :key="key" :value="key">{{ label }}</option></select></header>
    <p v-if="!items.length" class="empty-state">暂无样本。可在评测错误样本或推理结果中点击“收集并审核”。</p>
    <div v-for="item in items" :key="item.id" class="review-item"><input v-model="selected" type="checkbox" :value="item.id" :disabled="item.status!=='reviewed'||busy" :aria-label="`选择 ${item.name}`" /><button class="review-sample" :class="{selected:current?.id===item.id}" :disabled="busy" @click="choose(item.id)"><img :src="assetsApi.thumbnailUrl(item.asset_id,160)" alt="" /><span>{{ item.name }}<small>{{ labels[item.status] }}</small></span></button></div>
    <div class="training-pagination"><button class="text-button" :disabled="offset===0" @click="page(-1)">上一页</button><span>{{ Math.floor(offset/24)+1 }}</span><button class="text-button" :disabled="offset+24>=total" @click="page(1)">下一页</button></div>
  </section><section class="section-block review-editor-panel"><template v-if="current"><h2>{{ current.name }}</h2><p class="muted-copy">{{ current.ground_truth===null?'由模型预测初始化，尚不能作为真实标注':'由评测标注初始化，可对照预测进行修正' }} · {{ labels[current.status] }}</p>
    <fieldset :disabled="busy"><DetectionEditor :key="current.id" v-model="current.boxes" :asset-id="current.asset_id" :width="current.width" :height="current.height" :suggestions="suggestions" @update:model-value="dirty=true" /><label class="field-label">审核备注<textarea v-model="current.note" maxlength="2000" @input="dirty=true" /></label></fieldset>
    <details><summary>查看原预测与原标注</summary><pre class="annotation-example">{{ JSON.stringify({predictions:current.predictions,ground_truth:current.ground_truth},null,2) }}</pre></details>
    <div class="toolbar-actions"><button class="secondary-button" :disabled="busy" @click="save(false)">保存草稿</button><button class="primary-button compact-button" :disabled="busy" @click="save(true)">确认标注正确，审核通过</button><span v-if="dirty" class="muted-copy">有未保存修改</span></div>
    <p v-for="pub in current.publications" :key="pub.dataset_id" class="muted-copy">已生成 <RouterLink :to="`/training/datasets/${pub.dataset_id}`">数据集 v{{ pub.version }}</RouterLink>，后续修改不会影响该版本。</p>
  </template><p v-else class="empty-state">选择左侧样本开始审核。</p></section></div>
  <section class="section-block"><h2>将已审核样本生成新版本</h2><p>保留目标数据集的全部原图与划分；同内容图片更新标注，不重复添加。仅新增图片使用下方指定划分。</p><div class="training-form-grid"><label class="field-label">目标数据集版本<select v-model="datasetId" aria-label="审核目标数据集"><option value="">请选择</option><option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.name }} · v{{ d.version }}</option></select></label><label class="field-label">新增图片放入<select v-model="split"><option value="train">训练集</option><option value="val">验证集</option><option value="test">测试集</option></select></label></div>
    <p class="muted-copy">标注类别必须属于目标数据集。勾选队列中已审核的样本后生成版本。</p><label class="check-control"><input v-model="ack" type="checkbox" />我了解：使用评测样本改进模型后，不能再将这些样本作为独立验收数据。</label><button class="primary-button compact-button" :disabled="busy||dirty||!datasetId||!selected.length" @click="publish">生成新版本（{{ selected.length }} 张）</button>
    <p v-if="created" class="build-notice">已生成 {{ created.name }} v{{ created.version }} · <RouterLink :to="`/training/datasets/${created.id}`">查看数据</RouterLink> · <RouterLink :to="{path:'/training/new',query:{dataset:created.id}}">用此版本训练</RouterLink></p>
  </section>
</template>
<style scoped>
.check-control{width:auto;align-items:flex-start;margin:16px 0;line-height:1.6}.check-control input{flex-shrink:0}.review-editor-panel .toolbar-actions{align-items:center}.review-layout {display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}.review-item{display:flex;gap:8px;margin:10px 0}.review-sample{display:flex;gap:10px;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--text);text-align:left;min-width:0;width:100%}.review-sample.selected{border-color:#2563eb}.review-sample img{width:64px;height:58px;object-fit:cover}.review-sample span{overflow-wrap:anywhere;min-width:0}.review-sample small{display:block;color:var(--muted);margin-top:6px}.review-editor-panel fieldset{border:0;padding:0;min-width:0}.review-editor-panel h2{overflow-wrap:anywhere}.review-editor-panel textarea{min-height:65px}.review-editor-panel .toolbar-actions{margin:16px 0}@media(max-width:900px){.review-layout{grid-template-columns:minmax(0,1fr)}}
</style>
