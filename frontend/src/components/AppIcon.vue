<script setup lang="ts">
defineProps<{ name: 'overview' | 'devices' | 'model' | 'account' | 'arrow' | 'logout' | 'panel' | 'edit' | 'search' | 'chat' | 'close' }>()
const paths = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  devices: 'M3 12h4l3-7 4 14 3-7h4',
  model: 'M4 4v16h16 M7 14l4-4 4 2 5-7',
  account: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-2a8 8 0 0 1 16 0v2',
  arrow: 'M4 12h16 M14 6l6 6-6 6',
  logout: 'M9 4H4v16h5 M12 12h9 M16 7l5 5-5 5',
  panel: 'M3 4h18v16H3z M9 4v16 M5 8h2 M5 12h2',
  edit: 'M12 4H4v16h16v-8 M10 14l1-4L19 2l3 3-8 8z',
  search: 'M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 M15 15l6 6',
  chat: 'M4 3h16v13H9l-5 5z',
  close: 'M5 5l14 14 M19 5L5 19',
}
</script>
<template><svg aria-hidden="true" viewBox="0 0 24 24" class="app-icon" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path :d="paths[name]" /></svg></template>
