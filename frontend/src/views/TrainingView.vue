<script setup lang="ts">
import { ref } from 'vue'
import TrainingTaskTypes from '@/components/training/TrainingTaskTypes.vue'
const taskType = ref('object_detection')
</script>

<template>
  <header class="page-heading"><h1>训练中心</h1><p>先选择视觉任务类型，再进入训练或验证流程。已有任务的进度和结果在“训练任务”中查看。</p></header>
  <section aria-label="训练任务分类">
    <h2>任务类型</h2>
    <TrainingTaskTypes v-model="taskType" />
    <p class="muted-copy">当前已接入目标检测训练，其他任务类型正在规划，暂不可选。</p>
  </section>
  <div class="training-entry-grid">
    <section class="section-block training-entry-card">
      <h2>新建训练</h2>
      <p>选择算法、模型规格、数据版本和设备，完成参数配置后开始训练。</p>
      <span class="muted-copy">目标检测 · YOLO11 / YOLO26（五规格）· Faster R-CNN ResNet50-FPN</span>
      <RouterLink :to="{ path: '/training/new', query: { task_type: taskType } }" class="primary-button compact-button">新建训练</RouterLink>
    </section>
    <section class="section-block training-entry-card">
      <h2>独立验证</h2>
      <p>使用已有权重评估数据集，查看整体指标、分类别表现和结果图表。</p>
      <span class="muted-copy">支持训练集、验证集或测试集</span>
      <RouterLink :to="{ path: '/evaluations', query: { mode: 'single' } }" class="secondary-button compact-button">独立验证</RouterLink>
    </section>
  </div>
</template>

<style scoped>
.training-entry-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 24px; }
.training-entry-card { display: flex; flex-direction: column; align-items: flex-start; margin: 0; gap: 14px; }
.training-entry-card h2, .training-entry-card p { margin: 0; }
.training-entry-card p { color: var(--muted); line-height: 1.8; }
.training-entry-card a { margin-top: 8px; }
@media (max-width: 700px) { .training-entry-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
