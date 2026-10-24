<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { trainingApi, type TrainingReport } from '@/api/training'
import TrainingChart from '@/components/training/TrainingChart.vue'
import { errorMessage } from '@/utils/errors'
const route=useRoute(),reports=ref<TrainingReport[]>([]),error=ref(''),loading=ref(false),onlyDifferences=ref(true)
const ids=computed(()=>typeof route.query.ids==='string'?[...new Set(route.query.ids.split(',').filter(Boolean))]:[])
let sequence=0
async function load(){const seq=++sequence;error.value='';reports.value=[];if(ids.value.length<2||ids.value.length>4){error.value='请从训练任务中选择 2–4 个实验';return}loading.value=true;try{const result=await Promise.all(ids.value.map(id=>trainingApi.report(id)));if(seq===sequence)reports.value=result}catch(e){if(seq===sequence)error.value=errorMessage(e,'实验读取失败')}finally{if(seq===sequence)loading.value=false}}
watch(ids,load,{immediate:true})
const text=(v:unknown)=>v===undefined||v===null?'—':typeof v==='object'?JSON.stringify(v):String(v)
const params=computed(()=>[...new Set(reports.value.flatMap(r=>Object.keys(r.parameters)))].sort().map(key=>({key,values:reports.value.map(r=>text(r.parameters[key]))})).filter(row=>!onlyDifferences.value||new Set(row.values).size>1))
const warnings=computed(()=>{const result:string[]=[];if(reports.value.length<2)return result
 if(reports.value.some(r=>!r.dataset.sha256)||new Set(reports.value.map(r=>r.dataset.sha256)).size>1)result.push('数据版本内容不同或缺少哈希，指标不能直接作为模型排名。')
 if(new Set(reports.value.map(r=>r.algorithm)).size>1)result.push('不同算法的 P/R 工作点与预处理可能不同，请统一评测集与指标定义后比较。')
 if(new Set(reports.value.map(r=>r.operation)).size>1)result.push('包含不同执行方式：训练逐轮指标与独立验证指标分别展示，不直接排名。')
 if(new Set(reports.value.map(r=>JSON.stringify([r.parameters.imgsz,r.parameters.split??'val',r.parameters.conf,r.parameters.iou,r.parameters.max_det,r.parameters.nms,r.parameters.classes]))).size>1)result.push('验证尺寸、阈值或类别范围等配置存在差异，需统一评测条件后再选择模型。')
 if(new Set(reports.value.map(r=>JSON.stringify([r.node_id,r.gpu_device_ids]))).size>1)result.push('执行设备不同，耗时不代表同设备速度差异。')
 if(reports.value.some(r=>r.status!=='completed'))result.push('包含未完成或已停止的任务，当前结果可能不完整。')
 return result})
const metricKeys=[['metrics/mAP50-95(B)','mAP50-95'],['train/box_loss','训练框损失'],['train/cls_loss','训练分类损失']] as const
const epochs=computed(()=>Math.max(1,...reports.value.flatMap(r=>r.metrics.map(m=>m.epoch??0))))
function series(key:string){return reports.value.map((r,index)=>({label:`${index+1}. ${r.name}`,slot:index+1,points:r.metrics.filter(m=>Number.isFinite(m[key])&&Number.isFinite(m.epoch)).map(m=>[m.epoch!,m[key]!] as [number,number])}))}
const percent=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?`${(value*100).toFixed(2)}%`:'—'
const duration=(r:TrainingReport)=>r.started_at&&r.finished_at?`${((Date.parse(r.finished_at)-Date.parse(r.started_at))/60000).toFixed(1)} 分钟`:'—'
const classes=computed(()=>[...new Set(reports.value.flatMap(r=>r.evaluation?.per_class.map(c=>c.name)??[]))])
function download(kind:'json'|'csv'){
 let data:string
 if(kind==='json')data=JSON.stringify({warnings:warnings.value,reports:reports.value},null,2)
 else{const rows:unknown[][]=[['任务','模型','数据版本','状态','最佳逐轮 mAP50-95','训练耗时','服务器'],...reports.value.map(r=>[r.name,r.architecture,`${r.dataset.name??''} v${r.dataset.version??''}`,r.status,r.best?.['metrics/mAP50-95(B)']??'',duration(r),r.node_id]),[],['参数',...reports.value.map(r=>r.name)],...[...new Set(reports.value.flatMap(r=>Object.keys(r.parameters)))].sort().map(k=>[k,...reports.value.map(r=>text(r.parameters[k]))])];data='\ufeff'+rows.map(row=>row.map(v=>{let s=text(v);if(/^[=+@\-\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'}).join(',')).join('\r\n')}
 const link=document.createElement('a'),url=URL.createObjectURL(new Blob([data],{type:kind==='csv'?'text/csv;charset=utf-8':'application/json'}));link.href=url;link.download=`training-comparison.${kind}`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
</script>
<template><RouterLink class="back-link" to="/training/tasks">← 训练任务</RouterLink><header class="page-heading page-heading-actions"><div><h1>训练实验对比</h1><p>比较配置、数据版本和曲线。选出候选模型后，用统一测试集与设备做最终验收。</p></div><div v-if="reports.length" class="toolbar-actions"><button class="secondary-button" @click="download('csv')">下载对比 CSV</button><button class="secondary-button" @click="download('json')">下载完整 JSON</button></div></header>
  <p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="loading" class="empty-state">正在读取实验快照…</p><p v-for="warning in warnings" :key="warning" class="build-notice">{{ warning }}</p>
  <template v-if="reports.length"><section class="section-block"><h2>实验概况</h2><div class="table-scroll"><table class="data-table"><thead><tr><th>项目</th><th v-for="(r,i) in reports" :key="r.id">{{ i+1 }}. {{ r.name }}</th></tr></thead><tbody>
                                    <tr><th>模型 / 状态</th><td v-for="r in reports" :key="r.id">{{ r.architecture }} · {{ r.status_label }}</td></tr><tr><th>数据版本</th><td v-for="r in reports" :key="r.id">{{ r.dataset.name }} · v{{ r.dataset.version??'未记录' }}</td></tr><tr><th>逐轮最佳 mAP50-95</th><td v-for="r in reports" :key="r.id">{{ percent(r.best?.['metrics/mAP50-95(B)']) }} · 第 {{ r.best?.epoch??'—' }} 轮</td></tr><tr><th>独立/最终验证 mAP50-95</th><td v-for="r in reports" :key="r.id">{{ percent(r.evaluation?.metrics['metrics/mAP50-95(B)']) }}</td></tr><tr><th>实际执行 / 训练耗时</th><td v-for="r in reports" :key="r.id">{{ r.node_id }} · {{ r.parameters.device === 'cpu' ? 'CPU' : `GPU ${r.gpu_device_ids.join(',')||'未记录'}` }} · {{ duration(r) }}</td></tr><tr><th>操作</th><td v-for="r in reports" :key="r.id"><RouterLink :to="`/training/${r.id}/report`">完整报告</RouterLink> · <RouterLink :to="{path:'/training/new',query:{copy:r.id}}">复用配置</RouterLink></td></tr>
                                  </tbody></table></div><p class="muted-copy">缺失指标显示“—”，不按零分处理。训练总耗时包含训练过程开销，不等同于推理速度；速度对比请在相同设备上统一评测。</p></section>
    <section class="section-block"><header class="section-heading"><h2>参数差异</h2><label><input v-model="onlyDifferences" type="checkbox" />只看不同参数</label></header><div class="table-scroll"><table class="data-table"><thead><tr><th>参数</th><th v-for="r in reports" :key="r.id">{{ r.name }}</th></tr></thead><tbody><tr v-for="row in params" :key="row.key" :class="{different:new Set(row.values).size>1}"><th>{{ row.key }}</th><td v-for="(value,i) in row.values" :key="i">{{ value }}</td></tr></tbody></table></div><p v-if="!params.length" class="muted-copy">当前没有参数差异。</p></section>
    <section class="section-block"><h2>训练曲线叠加</h2><TrainingChart v-for="[key,title] in metricKeys" :key="key" :title="title" :series="series(key)" :epochs="epochs" /></section>
    <section class="section-block"><h2>分类别验证表现</h2><p v-if="!classes.length" class="muted-copy">这些实验未记录分类别验证结果。可在 <RouterLink to="/evaluations?mode=single">评测中心</RouterLink>补充独立验证。</p><div v-else class="table-scroll"><table class="data-table"><thead><tr><th>类别 / mAP50-95</th><th v-for="r in reports" :key="r.id">{{ r.name }}</th></tr></thead><tbody><tr v-for="label in classes" :key="label"><th>{{ label }}</th><td v-for="r in reports" :key="r.id">{{ percent(r.evaluation?.per_class.find(c=>c.name===label)?.map50_95) }}</td></tr></tbody></table></div></section>
  </template></template>
<style scoped>.table-scroll{overflow:auto}.data-table{min-width:600px}.data-table td{max-width:320px;overflow-wrap:anywhere}.different{background:var(--surface-subtle,#f3f6ff)}.section-block .training-chart{margin:20px 0}</style>
