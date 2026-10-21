<script setup lang="ts">
import { ref } from 'vue'

import type { SegmentationResult } from '@/types/task'

defineProps<{ imageUrl: string; result: SegmentationResult; confidence: number }>()
const hiddenMasks = ref<string[]>([])

function toggleMask(maskUri: string) {
  hiddenMasks.value = hiddenMasks.value.includes(maskUri)
    ? hiddenMasks.value.filter((item) => item !== maskUri)
    : [...hiddenMasks.value, maskUri]
}
</script>

<template>
  <div class="segmentation-renderer">
    <div class="mask-stage">
      <img :src="imageUrl" alt="原图" />
      <img
        v-for="segment in result.data.segments.filter((item) => item.score >= confidence && !hiddenMasks.includes(item.mask_uri))"
        :key="segment.mask_uri"
        class="mask-layer"
        :src="segment.mask_uri"
        :alt="segment.label"
      />
    </div>
    <div class="filter-chips">
      <button
        v-for="segment in result.data.segments"
        :key="segment.mask_uri"
        :class="{ disabled: hiddenMasks.includes(segment.mask_uri) }"
        @click="toggleMask(segment.mask_uri)"
      >{{ segment.label }} · {{ (segment.score * 100).toFixed(1) }}%</button>
    </div>
  </div>
</template>
