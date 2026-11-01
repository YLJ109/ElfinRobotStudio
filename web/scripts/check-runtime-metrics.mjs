// Offline duration statistics + real telemetry setup with fake scheduling/network.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { computed, ref, watch, reactive, toRefs, effectScope, nextTick } from 'vue'
import { createPerformanceComparison, performanceIdentity } from '../src/performanceComparison.js'
import { recordMetric, snapshotMetrics, resetMetrics, METRIC_NAMES, METRIC_WINDOW_SIZE } from '../src/runtimeMetrics.js'

test('fixed allowlist, duration-only data and explicit unsampled values', () => {
  resetMetrics()
  assert.deepEqual(METRIC_NAMES, ['http.request','ik.roundtrip','preview.roundtrip','state.decode','state.interval','scene.frame','scene.update','scene.render','longtask','scene.load','scene.prepare'])
  for (const value of Object.values(snapshotMetrics())) assert.deepEqual(value, { n: 0, p50: null, p95: null, max: null })
  for (const name of ['url', 'http://controller', 'secret', '__proto__', {}, null]) assert.equal(recordMetric(name, 12), false)
  for (const ms of [NaN, Infinity, -Infinity, -1, '5', null, undefined, true, {}]) assert.equal(recordMetric('http.request', ms), false)
  assert.equal(recordMetric('http.request', 0), true)
  assert.deepEqual(snapshotMetrics()['http.request'], { n: 1, p50: 0, p95: 0, max: 0 })
  assert.equal(Object.keys(snapshotMetrics()).length, 11)
})
test('nearest-rank quantiles, independent windows and detached snapshots', () => {
  resetMetrics()
  for (let i = 20; i > 0; i--) recordMetric('ik.roundtrip', i)
  recordMetric('preview.roundtrip', 3.5)
  const first = snapshotMetrics()
  assert.deepEqual(first['ik.roundtrip'], { n: 20, p50: 10, p95: 19, max: 20 })
  assert.deepEqual(first['preview.roundtrip'], { n: 1, p50: 3.5, p95: 3.5, max: 3.5 })
  first['ik.roundtrip'].n = 999
  first['http.request'] = { fake: 'source' }
  assert.equal(snapshotMetrics()['ik.roundtrip'].n, 20)
  assert.equal(snapshotMetrics()['http.request'].n, 0)
})
test('ring drops oldest samples, stays bounded and reset clears all history', () => {
  resetMetrics()
  for (let i = 0; i < METRIC_WINDOW_SIZE; i++) recordMetric('scene.frame', 9999)
  for (let i = 0; i < METRIC_WINDOW_SIZE; i++) recordMetric('scene.frame', 16)
  assert.deepEqual(snapshotMetrics()['scene.frame'], { n: METRIC_WINDOW_SIZE, p50: 16, p95: 16, max: 16 })
  for (let i = 0; i < 20000; i++) recordMetric('state.interval', i % 20)
  assert.equal(snapshotMetrics()['state.interval'].n, METRIC_WINDOW_SIZE)
  resetMetrics()
  for (const value of Object.values(snapshotMetrics())) assert.equal(value.n, 0)
  recordMetric('scene.update', .25)
  assert.deepEqual(snapshotMetrics()['scene.update'], { n: 1, p50: .25, p95: .25, max: .25 })
})

const source = readFileSync(new URL('../src/panels/TelemetryPanel.vue', import.meta.url), 'utf8')
const descriptor = parse(source).descriptor
function panelHarness(overrides={}) {
  const scope = effectScope(), mounted = [], unmounted = [], activated = [], deactivated = [], timers = new Map(), events = new Map()
  const props = reactive({ active: true })
  const doc = { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: (name, fn) => { if (events.get(name) === fn) events.delete(name) } }
  const counts = { read: 0, reset: 0, network: 0 }
  const world = reactive({ connected: true, snap: { mode: 'sim',session_id:'safe-session',tools:{mounted:{tool_id:'vacuum-reference',revision:1},visual:{kind:'vacuum-reference'}},settings:{sim_tool:{kind:'vacuum-reference'}} }, tele: null,
    play: { on: false, loaded: false, n: 0, cols: [], t: [] },
    call() { counts.network++; throw new Error('network forbidden') }, playLoad() { counts.network++; throw new Error('network forbidden') } })
  let sequence = 0
  const dependencies = { computed, ref, watch, storeToRefs: toRefs, useWorld: () => world, Spark: {}, TaskTabs: {},
    createPerformanceComparison, performanceIdentity, METRIC_WINDOW_SIZE, snapshotMetrics: () => { counts.read++; return snapshotMetrics() }, resetMetrics: () => { counts.reset++; resetMetrics() },
    document: doc, window:{innerWidth:1280,innerHeight:800,devicePixelRatio:1}, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn),
    onActivated: fn => activated.push(fn), onDeactivated: fn => deactivated.push(fn),
    setInterval(fn, ms) { timers.set(++sequence, { fn, ms }); return sequence }, clearInterval: id => timers.delete(id),
    fetch() { counts.network++; throw new Error('network forbidden') },...overrides,
  }
  const code = compileScript(descriptor, { id: 'runtime-metrics' }).content.replace(/^import[\s\S]*?from ['"][^'"]+['"];?\r?\n/gm, '').replace('export default', 'return')
  const component = new Function(...Object.keys(dependencies), code)(...Object.values(dependencies))
  const api = scope.run(() => component.setup(props, { expose() {} }))
  let closed = false
  return { api, props, counts, timers, events, doc, mounted, activated, deactivated,
    dispose() { if (closed) return; closed = true; unmounted.forEach(fn => fn()); scope.stop() } }
}
test('performance UI compiles, explicitly labels CPU/GPU limits and keeps the existing tabs', () => {
  const script = compileScript(descriptor, { id: 'runtime-metrics' })
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'TelemetryPanel.vue', id: 'runtime-metrics', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'TelemetryPanel.vue', id: 'runtime-metrics', scoped: true }).errors, [])
  for (const phrase of ['未采样', 'GPU 未测量', '不是 GPU 执行耗时', '重置样本']) assert.ok(source.includes(phrase))
  const h = panelHarness()
  try { assert.deepEqual(h.api.tabs.map(tab => tab.id), ['health','trend','io','playback','performance']) }
  finally { h.dispose() }
})
test('only active visible performance tab reads snapshots; reset is local and leaving cancels timers', async () => {
  resetMetrics(); recordMetric('http.request', 23)
  const h = panelHarness()
  try {
    h.mounted.forEach(fn => fn())
    assert.equal(h.counts.read, 0); assert.equal(h.timers.size, 0)
    h.api.tab.value = 'performance'; await nextTick()
    assert.equal(h.counts.read, 1); assert.equal(h.timers.size, 1)
    assert.equal([...h.timers.values()][0].ms, 1000)
    assert.equal(h.api.runtimeStats.value['http.request'].p50, 23)
    const oldTick = [...h.timers.values()][0].fn
    h.api.tab.value = 'health'; await nextTick(); oldTick()
    assert.equal(h.counts.read, 1); assert.equal(h.timers.size, 0)
    h.api.tab.value = 'performance'; await nextTick()
    h.api.clearMetrics()
    assert.equal(h.counts.reset, 1)
    assert.equal(h.api.runtimeStats.value['http.request'].n, 0)
    assert.equal(h.counts.network, 0)
    h.props.active = false; await nextTick()
    const reads = h.counts.read; oldTick(); h.api.clearMetrics()
    assert.equal(h.timers.size, 0); assert.equal(h.counts.read, reads); assert.equal(h.counts.reset, 1)
  } finally { h.dispose() }
  assert.equal(h.timers.size, 0); assert.equal(h.events.size, 0)
})
test('visibility, cached-page deactivation and unmount prevent background reads or stale callbacks', async () => {
  const h = panelHarness()
  try {
    h.mounted.forEach(fn => fn()); h.api.tab.value = 'performance'; await nextTick()
    const oldTick = [...h.timers.values()][0].fn
    h.doc.hidden = true; h.events.get('visibilitychange')()
    const hiddenReads = h.counts.read; oldTick()
    assert.equal(h.timers.size, 0); assert.equal(h.counts.read, hiddenReads)
    h.doc.hidden = false; h.events.get('visibilitychange')()
    assert.equal(h.timers.size, 1)
    h.deactivated.forEach(fn => fn()); const inactiveReads = h.counts.read
    oldTick(); assert.equal(h.counts.read, inactiveReads); assert.equal(h.timers.size, 0)
    h.activated.forEach(fn => fn()); assert.equal(h.timers.size, 1)
    h.dispose(); const finalReads = h.counts.read; oldTick()
    assert.equal(h.counts.read, finalReads); assert.equal(h.timers.size, 0); assert.equal(h.events.size, 0)
    assert.equal(h.counts.network, 0)
  } finally { h.dispose() }
})

test('actual performance component explicitly captures both sides and downloads detached local JSON',async()=>{
  let calls=0,download='',released=0
  const build={verified:true,backend_sha256:'a'.repeat(64),frontend_sha256:'b'.repeat(64)}
  let buildReads=0
  const h=panelHarness({fetch:async(path)=>{if(path==='/api/build'){buildReads++;return {ok:true,json:async()=>build}}assert.equal(path,'/api/vision/pipeline');calls++;return {ok:true,json:async()=>({ok:true,enabled:true,source:'virtual',session:'safe',epoch:1,server_now:10,result:{clock_domain:'server',captured_at:9.9},stats:{submitted:calls,inferred:calls}})}},
    URL:{createObjectURL:blob=>{download=blob;return 'blob:safe'},revokeObjectURL:()=>released++}})
  let clicked=0;h.doc.createElement=()=>({click(){clicked++}})
  try {
    h.mounted.forEach(fn=>fn());h.api.tab.value='performance';await nextTick();assert.equal(calls,0)
    for(const slot of ['before','after']){await h.api.startComparison(slot);await new Promise(resolve=>setImmediate(resolve));h.api.finishComparison()}
    assert.equal(h.api.comparisonReady.value,true);h.api.exportComparison()
    assert.equal(clicked,1);assert.equal(released,1);assert.equal(buildReads,2)
    const out=JSON.parse(await download.text());assert.equal(out.before.samples.length,1);assert.equal(out.after.samples.length,1)
    assert.deepEqual(JSON.parse(out.before.identity).build,{backend_sha256:build.backend_sha256,frontend_sha256:build.frontend_sha256});assert.equal(JSON.parse(out.before.identity).tool.mounted.tool_id,'vacuum-reference');assert.equal(out.before.samples[0].vision.gpu_percent,null);assert.ok(!JSON.stringify(out).includes('credentials'))
  }finally{h.dispose()}
})

test('hidden performance page aborts its pending read and cannot publish a late sample',async()=>{
  let resolve,signal
  const h=panelHarness({fetch:(_path,options)=>{signal=options.signal;return new Promise(done=>resolve=done)}})
  try {
    h.mounted.forEach(fn=>fn());h.api.tab.value='performance';await nextTick();h.api.startComparison('before')
    h.doc.hidden=true;h.events.get('visibilitychange')();assert.equal(signal.aborted,true)
    resolve({ok:true,json:async()=>({ok:true})});await new Promise(done=>setImmediate(done))
    assert.equal(h.api.comparison.active,false);assert.equal(h.api.comparisonSlot.value,'');assert.equal(h.api.comparison.before,null)
  }finally{h.dispose()}
})

test('actual component refuses absent unverified failed and changed build identities before sampling',async()=>{
  const valid={verified:true,backend_sha256:'a'.repeat(64),frontend_sha256:'b'.repeat(64)}
  let build=valid,failed=false,visionReads=0
  const h=panelHarness({fetch:async path=>path==='/api/build'?{ok:!failed,json:async()=>build}:{ok:true,json:async()=>{visionReads++;return {ok:true,enabled:false,source:null,session:null,epoch:0}}}})
  try {
    h.mounted.forEach(fn=>fn());h.api.tab.value='performance';await nextTick()
    for(const value of [{},{...valid,verified:false},{...valid,frontend_sha256:null}]){
      build=value;await h.api.startComparison('before');assert.equal(h.api.comparison.active,false);assert.match(h.api.comparisonNotice.value,/已验证版本/)
    }
    failed=true;await h.api.startComparison('before');assert.match(h.api.comparisonNotice.value,/版本读取失败/);assert.equal(visionReads,0)
    failed=false;build=valid;await h.api.startComparison('before');await new Promise(done=>setImmediate(done));h.api.finishComparison()
    const reads=visionReads;build={...valid,frontend_sha256:'c'.repeat(64)};await h.api.startComparison('after')
    assert.equal(h.api.comparison.active,false);assert.match(h.api.comparisonNotice.value,/版本不一致/);assert.equal(visionReads,reads);assert.equal(h.api.comparison.after,null)
  }finally{h.dispose()}
})

test('leaving and unmounting abort in-flight version verification and cannot start late sampling',async()=>{
  for(const leave of ['page','unmount']){
    let resolve,signal
    const h=panelHarness({fetch:(_path,options)=>{signal=options.signal;return new Promise(done=>resolve=done)}})
    try {
      h.mounted.forEach(fn=>fn());h.api.tab.value='performance';await nextTick();const pending=h.api.startComparison('before')
      if(leave==='page'){h.api.tab.value='health';await nextTick()}else h.dispose()
      assert.equal(signal.aborted,true)
      resolve({ok:true,json:async()=>({verified:true,backend_sha256:'a'.repeat(64),frontend_sha256:'b'.repeat(64)})});await pending
      assert.equal(h.api.comparison.active,false);assert.equal(h.api.comparison.before,null)
    }finally{h.dispose()}
  }
})
