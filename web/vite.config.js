import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
import { NodeTypes, createCompoundExpression } from '@vue/compiler-dom'

const rawTextContexts = new WeakSet()

// Compiler-owned coverage includes future SFCs without scanning or patching
// the live DOM. Leave text/range controls and dynamic input types alone.
export function numberInputPrecisionTransform(node, context) {
  if (node.type === NodeTypes.ELEMENT) {
    for (const prop of node.props) {
      if (prop.type === NodeTypes.DIRECTIVE && prop.name === 'bind' && prop.arg?.isStatic &&
          ['title','aria-label'].includes(prop.arg.content) && prop.exp) {
        prop.exp = createCompoundExpression(['_ctx.$displayValue(', prop.exp, ')'])
      }
    }
  }
  if (node.type === NodeTypes.ELEMENT && ['pre','code','textarea','script','style'].includes(node.tag)) {
    const nested = rawTextContexts.has(context)
    rawTextContexts.add(context)
    return () => { if (!nested) rawTextContexts.delete(context) }
  }
  if (node.type === NodeTypes.INTERPOLATION && !rawTextContexts.has(context)) {
    node.content = createCompoundExpression(['_ctx.$displayValue(', node.content, ')'])
    return
  }
  if (node.type !== NodeTypes.ELEMENT || node.tag !== 'input') return
  const numeric = node.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === 'type' && prop.value?.content === 'number')
  if (!numeric || node.props.some(prop => prop.type === NodeTypes.DIRECTIVE && prop.name === 'two-decimals')) return
  node.props.push({ type: NodeTypes.DIRECTIVE, name: 'two-decimals', rawName: 'v-two-decimals',
    exp: undefined, arg: undefined, modifiers: [], loc: node.loc })
}

const devOrigins = new Set(['http://localhost:5173', 'http://127.0.0.1:5173', 'http://[::1]:5173'])
export function configureApiProxy(proxy) {
  proxy.on('proxyReq', (outgoing, incoming) => {
    // 只转换明确的开发页来源，不替任意外部页面伪造可信 Origin。
    if (devOrigins.has(incoming.headers.origin)) outgoing.setHeader('origin', 'http://127.0.0.1:8710')
  })
}

// 后端在 8710；开发时把 /api 和 /ws 代理过去，生产构建后由 FastAPI 直接托管 dist
export default defineConfig({
  plugins: [vue({ template: { compilerOptions: { nodeTransforms: [numberInputPrecisionTransform] } } })],
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
