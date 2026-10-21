<script setup lang="ts">
import type { ClassificationResult } from '@/types/task'

defineProps<{ result: ClassificationResult; confidence: number }>()
</script>

<template>
  <div class="classification-bars">
    <div v-for="prediction in result.data.predictions.filter((item) => item.score >= confidence)" :key="prediction.label">
      <header>
        <strong>{{ prediction.label }}</strong>
        <span>{{ (prediction.score * 100).toFixed(1) }}%</span>
      </header>
      <progress :value="prediction.score" max="1" />
    </div>
  </div>
</template>
