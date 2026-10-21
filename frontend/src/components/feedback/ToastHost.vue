<script setup lang="ts">
import { useToasts } from '@/composables/useToast'

const { toasts, dismiss } = useToasts()
const icons = { success: '✓', error: '!', info: 'i' }
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="item in toasts" :key="item.id" class="toast" :class="`toast-${item.kind}`" :role="item.kind === 'error' ? 'alert' : 'status'">
        <span class="toast-icon" aria-hidden="true">{{ icons[item.kind] }}</span>
        <p>{{ item.message }}</p>
        <button type="button" class="toast-close" aria-label="关闭提示" @click="dismiss(item.id)">×</button>
      </div>
    </TransitionGroup>
  </div>
</template>
