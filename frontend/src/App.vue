<script setup lang="ts">
import AppShell from '@/components/layout/AppShell.vue'
import ToastHost from '@/components/feedback/ToastHost.vue'
</script>

<template>
  <RouterView v-if="$route.meta.public" />
  <AppShell v-else><RouterView /></AppShell>
  <ToastHost />
</template>
