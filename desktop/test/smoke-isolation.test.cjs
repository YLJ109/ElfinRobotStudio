const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { EventEmitter } = require('node:events')

// Execute the real CommonJS implementation with isolated process arguments and
// mocked I/O. No Python/World, real network, user data or log files are opened.
const filename = path.resolve(__dirname, '../backend.cjs')
const source = fs.readFileSync(filename, 'utf8')

function harness(argv, serviceIdentity = {version:1,backend_sha256:'source',frontend_sha256:'web',verified:true}) {
  const root = path.resolve(__dirname, '__mock_source__')
  const userData = path.resolve(__dirname, '__mock_user_data__')
  const calls = { fetch: [], spawn: [], exists: [], mkdir: [], logs: [] }
  const child = new EventEmitter()
  Object.assign(child, { pid: 12345, exitCode: null, stdout: new EventEmitter(), stderr: new EventEmitter() })
  child.kill = () => { throw new Error('Unexpected process kill') }
  const compatible = { session_id: 'existing-service', joints: { names: ['J1', 'J2', 'J3', 'J4', 'J5', 'J6'] }, hand_follow: {} }
  const env = { ELFIN_DATA_DIR: path.join(root, 'must-not-use-in-smoke'), TEST_SENTINEL: 'preserved' }
  const dependencies = {
    'node:path': path,
    'node:crypto': require('node:crypto'),
    'node:fs': {
      existsSync(file) { calls.exists.push(file); return path.basename(file)!=='data-selection.json' },
      mkdirSync(...args) { calls.mkdir.push(args) },
    },
    'node:child_process': {
      spawn(...args) {
        calls.spawn.push(args)
        queueMicrotask(() => child.stdout.emit('data', Buffer.from(
          JSON.stringify({ type: 'desktop-listening', port: 19876 }) + '\n')))
        return child
      },
    },
    './logSink.cjs': {
      BoundedLog: class {
        constructor(file) { calls.logs.push(file) }
        write() {}
        async end() {}
      },
    },
    './buildIdentity.cjs': {
      currentIdentity:()=>({version:1,backend_sha256:'source',frontend_sha256:'web'}),
      compatible:(expected,actual)=>actual?.verified===true && expected.backend_sha256===actual.backend_sha256 && expected.frontend_sha256===actual.frontend_sha256,
    },
    './dataSelection.cjs': {selectedData:directory=>path.join(directory,'data')},
  }
  const module = { exports: {} }
  const context = vm.createContext({
    module, exports: module.exports,
    require(name) {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency: ${name}`)
      return dependencies[name]
    },
    process: { argv: ['node', 'desktop/main.cjs', ...argv], env },
    AbortSignal, setTimeout,
    fetch: async (url, options) => {
      calls.fetch.push({ url, options })
      let value
      if (url === 'http://127.0.0.1:8710/api/state') value = compatible
      else if (url === 'http://127.0.0.1:8710/api/build') value = serviceIdentity
      else if (url === 'http://127.0.0.1:19876/api/desktop/status') value = { ready: true, pid: child.pid }
      else if (url === 'http://127.0.0.1:19876/api/desktop/shutdown') {
        value = { ok: true, settled: true }
        child.exitCode = 0; child.emit('exit', 0); child.emit('close', 0)
      } else if (url === 'http://127.0.0.1:8710/api/points/stop') value = { ok: true, settled: true }
      else throw new Error(`Unexpected request: ${url}`)
      return { ok: true, json: async () => value }
    },
  })
  new vm.Script(source, { filename }).runInContext(context)
  const session = new module.exports.BackendSession({ root, userData, resources: path.join(root, 'resources'), packaged: false })
  return { session, calls, root, userData, child, env }
}

test('source launch without smoke reuses compatible 8710 without spawning or writing files', { timeout: 3000 }, async () => {
  const { session, calls, userData } = harness([])
  assert.equal(await session.start(), 'http://127.0.0.1:8710')
  assert.equal(session.owned, false)
  assert.equal(session.child, null)
  assert.equal(calls.spawn.length, 0)
  assert.deepEqual(calls.exists, [path.join(userData,'data-selection.json')])
  assert.deepEqual(calls.mkdir, [])
  assert.deepEqual(calls.logs, [])
  assert.deepEqual(calls.fetch.map(call => call.url), ['http://127.0.0.1:8710/api/state','http://127.0.0.1:8710/api/build'])
  await session.close()
  assert.equal(calls.fetch.at(-1).url, 'http://127.0.0.1:8710/api/points/stop')
  assert.equal(calls.fetch.at(-1).options.method, 'POST')
})

test('source --smoke-output never probes/reuses 8710 and spawns with userData/data', { timeout: 3000 }, async () => {
  const { session, calls, root, userData, child, env } = harness(['--other-flag', '--smoke-output=mock-smoke.json'])
  assert.equal(await session.start(), 'http://127.0.0.1:19876')
  assert.equal(session.owned, true)
  assert.equal(session.child, child)
  assert.equal(calls.spawn.length, 1)
  const [executable, args, options] = calls.spawn[0]
  assert.equal(executable, path.join(root, 'backend', '.venv', 'Scripts', 'python.exe'))
  assert.deepEqual(Array.from(args), ['-B', path.join(root, 'desktop', 'backend_entry.py')])
  assert.equal(options.cwd, root)
  assert.equal(options.windowsHide, true)
  assert.equal(options.env.ELFIN_DATA_DIR, path.join(userData, 'data'))
  assert.notEqual(options.env.ELFIN_DATA_DIR, env.ELFIN_DATA_DIR)
  assert.equal(env.ELFIN_DATA_DIR, path.join(root, 'must-not-use-in-smoke'), 'parent environment was mutated')
  assert.equal(options.env.TEST_SENTINEL, 'preserved')
  assert.equal(options.env.ELFIN_DESKTOP, '1')
  assert.equal(options.env.ELFIN_DESKTOP_KEY, session.key)
  assert.deepEqual(calls.exists, [path.join(userData,'data-selection.json'),executable])
  assert.equal(calls.mkdir[0][0], path.join(userData, 'logs'))
  assert.deepEqual(calls.logs, [path.join(userData, 'logs', 'backend.log')])
  assert.deepEqual(calls.fetch.map(call => call.url), ['http://127.0.0.1:19876/api/desktop/status'])
  assert.equal(calls.fetch[0].options.headers['x-elfin-desktop-key'], session.key)
  assert.equal(await session.start(), 'http://127.0.0.1:19876')
  assert.equal(calls.spawn.length, 1, 'repeated start spawned a second backend')
  await session.close()
  assert.equal(calls.fetch.at(-1).url, 'http://127.0.0.1:19876/api/desktop/shutdown')
  assert.equal(calls.fetch.at(-1).options.headers['x-elfin-desktop-key'], session.key)
  assert.ok(calls.fetch.every(call => !call.url.startsWith('http://127.0.0.1:8710/')),
    'smoke must never contact the existing service, even when it is compatible')
})

test('a stale source or production page starts a private backend without stopping the old service',async()=>{
  for(const identity of [{version:1,backend_sha256:'old',frontend_sha256:'web',verified:true},
      {version:1,backend_sha256:'source',frontend_sha256:'old',verified:true},{version:1}]){
    const {session,calls}=harness([],identity)
    assert.equal(await session.start(),'http://127.0.0.1:19876')
    assert.equal(session.owned,true);assert.equal(calls.spawn.length,1)
    assert.ok(calls.fetch.every(call=>!call.url.endsWith('/api/points/stop')))
    await session.close()
    assert.ok(calls.fetch.every(call=>!call.url.startsWith('http://127.0.0.1:8710/api/points')))
  }
})
