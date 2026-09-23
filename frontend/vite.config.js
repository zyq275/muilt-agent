import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端地址（TDD §8.3：开发代理到 FastAPI 8000 端口）
const BACKEND = 'http://localhost:8000'
const BACKEND_WS = 'ws://localhost:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // REST：/api/* → FastAPI
      '/api': {
        target: BACKEND,
        changeOrigin: true,
      },
      // WebSocket：/ws/* → FastAPI
      //
      // ws: true 是关键，缺了它 vite 会按普通 HTTP 转发，
      // 握手阶段就直接失败（浏览器报 "WebSocket connection failed"）。
      '/ws': {
        target: BACKEND_WS,
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
