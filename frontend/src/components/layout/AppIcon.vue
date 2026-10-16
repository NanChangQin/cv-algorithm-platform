<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  home: 'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  layers: 'm12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  image: 'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm-1 13 5-5 4 4 3-3 6 6M15 7h.01',
  server: 'M4 3h16v7H4Zm0 11h16v7H4ZM7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6',
  tasks: 'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',
  compare: 'M4 4h6v16H4ZM14 4h6v16h-6Z',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-6v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
  workflow: 'M3 3h6v6H3Zm12 12h6v6h-6ZM9 6h7a2 2 0 0 1 2 2v7M6 9v7a2 2 0 0 0 2 2h7',
  users: 'M8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7-8a4 4 0 0 1 0 8M1 21v-3a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v3m3-7a4 4 0 0 1 5 4v3',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'm6 6 12 12M6 18 18 6',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
}
</script>

<template>
  <svg class="app-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path :d="paths[name] ?? paths.layers" />
  </svg>
</template>
