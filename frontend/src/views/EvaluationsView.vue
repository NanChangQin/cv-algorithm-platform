<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import EvaluationComparison from '@/components/training/EvaluationComparison.vue'
import { trainingApi, trainingStatusLabel, type TrainingRunSummary } from '@/api/training'
import { sessionRole } from '@/api/auth'
import { errorMessage } from '@/utils/errors'
const route=useRoute(),router=useRouter(),runs=shallowRef<TrainingRunSummary[]>([]),error=ref('')
const mode=computed(()=>route.query.mode==='single'?'single':'compare')
const canTrain=computed(()=>['admin','developer'].includes(sessionRole.get()??''))
onMounted(async()=>{if(canTrain.value)try{runs.value=(await trainingApi.runs()).filter(r=>r.progress?.operation==='val')}catch(e){error.value=errorMessage(e,'验证记录读取失败')}})
</script>
<template>
  <header class="page-heading page-heading-actions"><div><h1>评测中心</h1><p>统一选择评测目的、固定数据版本，查看指标并分析错误样本。</p></div><RouterLink v-if="canTrain" class="secondary-button" to="/training/reviews">问题样本与审核</RouterLink></header>
  <div class="toolbar-actions evaluation-modes"><button class="secondary-button" :aria-pressed="mode==='single'" @click="router.replace({query:{mode:'single'}})">单模型完整验证</button><button class="secondary-button" :aria-pressed="mode==='compare'" @click="router.replace({query:{mode:'compare'}})">多模型对比 / 错误分析</button></div>
  <template v-if="mode==='single'"><section class="section-block"><h2>单模型完整验证</h2><p>针对 YOLO / Faster R-CNN 检测权重，使用固定数据版本，输出 COCO mAP、分类别指标与混淆矩阵。不更新模型权重。</p><p class="muted-copy">需要逐图片误检 / 漏检分析时，将权重发布为模型后使用“多模型对比 / 错误分析”，也可以只选一个模型。</p><RouterLink v-if="canTrain" class="primary-button compact-button" :to="{path:'/training/new',query:{operation:'val'}}">配置验证任务</RouterLink><p v-else>此功能需要算法开发者或管理员权限。</p></section><p v-if="error" class="alert">{{ error }}</p><section class="section-block"><h2>验证记录</h2><p v-if="!runs.length" class="empty-state">暂无独立验证任务。</p><ul class="training-list"><li v-for="run in runs" :key="run.id"><RouterLink :to="`/training/${run.id}/report`">{{ run.name }} · {{ trainingStatusLabel[run.status] }}</RouterLink></li></ul></section></template>
  <EvaluationComparison v-else />
</template>
<style scoped>.evaluation-modes{margin-bottom:24px}.evaluation-modes [aria-pressed=true]{border-color:#2563eb;color:#2563eb}.section-block p{line-height:1.8}.training-list li{padding:12px 0}</style>
