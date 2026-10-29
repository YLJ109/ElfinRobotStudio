import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

const devOrigins = new Set(['http://localhost:5173', 'http://127.0.0.1:5173', 'http://[::1]:5173'])
export function configureApiProxy(proxy) {
  proxy.on('proxyReq', (outgoing, incoming) => {
    // 只转换明确的开发页来源，不替任意外部页面伪造可信 Origin。
    if (devOrigins.has(incoming.headers.origin)) outgoing.setHeader('origin', 'http://127.0.0.1:8710')
  })
}

// 后端在 8710；开发时把 /api 和 /ws 代理过去，生产构建后由 FastAPI 直接托管 dist
export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8710', changeOrigin: true, configure: configureApiProxy },
      '/ws': { target: 'ws://127.0.0.1:8710', ws: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1600, rollupOptions: { input: {
    main: fileURLToPath(new URL('./index.html', import.meta.url)),
    vision: fileURLToPath(new URL('./vision.html', import.meta.url)),
  } } },
})
