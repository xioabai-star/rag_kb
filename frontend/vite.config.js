import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式：Vite :5173 → 代理 /api 到 FastAPI :8002（避免跨域）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../app/static/dist', // 生产构建直接输出到 FastAPI 挂载目录
    emptyOutDir: true,
  },
})
