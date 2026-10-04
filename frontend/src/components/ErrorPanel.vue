<script setup lang="ts">
defineProps<{ message: string; retry?: boolean }>()
defineEmits<{ retry: [] }>()
</script>
<template>
  <div v-if="message" role="alert" class="error-panel">
    <strong>操作未完成</strong><p>{{ message }}</p>
    <el-button v-if="retry" size="small" @click="$emit('retry')">重新请求</el-button>
  </div>
</template>
