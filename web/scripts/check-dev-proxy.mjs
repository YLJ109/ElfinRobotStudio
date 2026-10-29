// 只运行代理配置函数，不启动服务、不打开浏览器。
import test from 'node:test'
import assert from 'node:assert/strict'
import devConfig, { configureApiProxy } from '../vite.config.js'

test('development models use current public assets, not the backend old dist',()=>{
  assert.ok(devConfig.server.proxy['/api'])
  assert.ok(devConfig.server.proxy['/ws'])
  assert.equal(devConfig.server.proxy['/models'],undefined)
})

test('development proxy rewrites only known local development origins', () => {
  let handler
  configureApiProxy({ on: (name, fn) => { assert.equal(name, 'proxyReq'); handler = fn } })
  for (const origin of ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://[::1]:5173']) {
    const headers = {}
    handler({ setHeader: (key, value) => { headers[key] = value } }, { headers: { origin } })
    assert.deepEqual(headers, { origin: 'http://127.0.0.1:8710' })
  }
})

test('external or absent origins are never promoted to trusted origins', () => {
  let handler
  configureApiProxy({ on: (_name, fn) => { handler = fn } })
  for (const origin of [undefined, 'https://evil.invalid', 'http://localhost:51730', 'null']) {
    handler({ setHeader: () => assert.fail('rewrote an untrusted origin') }, { headers: { origin } })
  }
})
