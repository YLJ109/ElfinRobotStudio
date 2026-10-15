import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端在 8710；开发时把 /api 和 /ws 代理过去，生产构建后由 FastAPI 直接托管 dist
export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8710', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8710', ws: true },
      '/models': { target: 'http://127.0.0.1:8710', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1600 },
})
