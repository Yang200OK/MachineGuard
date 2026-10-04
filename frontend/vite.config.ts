import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本机直接通过已授权 CORS 调用 API，公开部署显式配置 API 地址。
export default defineConfig({
  plugins: [vue()],
  build: { target: 'es2022' },
})
