// Vite 配置：开发服务器将 /api 代理到后端 FastAPI（含 WebSocket）
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.GEOAGENT_DEV_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
