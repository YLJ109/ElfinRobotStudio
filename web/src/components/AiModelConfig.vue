<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { allProviders, clearAiConfig, CUSTOM_PREFIX, DEFAULT_PROVIDER_ID, isConfigured, maskKey,
  pushAiConfig, readAiConfig, removeCustomProvider, saveCustomProvider, testAiConnection,
  writeAiConfig } from '../aiProviderCatalog.js'

const emit = defineEmits(['close', 'change'])

const saved = ref(readAiConfig())
const providers = ref(allProviders())
const providerId = ref(saved.value?.providerId || DEFAULT_PROVIDER_ID)
const modelId = ref(saved.value?.model || '')
const baseUrl = ref(saved.value?.baseUrl || '')
const apiKey = ref(saved.value?.apiKey || '')
const customName = ref('')
const showKey = ref(false)
const busy = ref(false), testing = ref(false)
const error = ref(''), note = ref('')

const provider = computed(() => providers.value.find((p) => p.id === providerId.value) || null)
const isCustom = computed(() => providerId.value.startsWith(CUSTOM_PREFIX))
const savedConfigured = computed(() => isConfigured(saved.value))

function applyProviderDefaults() {
  const p = provider.value
  if (!p) { baseUrl.value = ''; modelId.value = ''; return }
  if (isCustom.value) {
    customName.value = p.name || ''
    baseUrl.value = p.baseUrl || ''
    modelId.value = p.models?.[0]?.id || ''
    return
  }
  baseUrl.value = p.baseUrl || ''
  modelId.value = p.models?.[0]?.id || ''
}
// 首次进入：已保存的配置原样回填；没保存过就落在默认厂商（智谱）的默认型号上。
if (!saved.value) applyProviderDefaults()
else if (saved.value.providerId.startsWith(CUSTOM_PREFIX)) {
  const p = provider.value
  customName.value = saved.value.providerName || p?.name || ''
}

watch(providerId, () => {
  error.value = ''; note.value = ''
  applyProviderDefaults()
  // 不同厂商的 Key 不通用，切厂商就别把上一家的 Key 留在输入框里。
  apiKey.value = ''
})
watch(modelId, () => { error.value = ''; note.value = '' })

function addCustom() {
  const entry = { id: CUSTOM_PREFIX + Date.now().toString(36), name: '自定义厂商', baseUrl: '', models: [{ id: '', label: '' }] }
  saveCustomProvider(entry)
  providers.value = allProviders()
  providerId.value = entry.id
}
function dropCustom() {
  if (!isCustom.value) return
  removeCustomProvider(providerId.value)
  providers.value = allProviders()
  providerId.value = DEFAULT_PROVIDER_ID
}

async function save() {
  if (busy.value) return
  error.value = ''; note.value = ''
  const model = String(modelId.value || '').trim()
  const base = String(baseUrl.value || '').trim()
  const key = String(apiKey.value || '').trim()
  if (!model) { error.value = '请选择或填写模型 id'; return }
  if (!/^https?:\/\/\S+/i.test(base)) { error.value = 'Base URL 必须是 http(s) 地址'; return }
  if (!key) { error.value = '请填写 API Key'; return }
  busy.value = true
  try {
    let name = provider.value?.name || providerId.value
    if (isCustom.value) {
      name = String(customName.value || '').trim() || '自定义厂商'
      saveCustomProvider({ id: providerId.value, name, baseUrl: base.replace(/\/+$/, ''), models: [{ id: model, label: model }] })
      providers.value = allProviders()
    }
    const config = writeAiConfig({ providerId: providerId.value, providerName: name, model,
                                   baseUrl: base.replace(/\/+$/, ''), apiKey: key })
    const pushed = await pushAiConfig(config)
    saved.value = config
    emit('change', config)
    if (pushed?.ok === false) { error.value = `已存到本机，但下发给后端失败：${pushed.why || '未知原因'}`; return }
    emit('close')
  } finally { busy.value = false }
}

async function testConnection() {
  if (testing.value) return
  error.value = ''; note.value = ''
  const base = String(baseUrl.value || '').trim().replace(/\/+$/, '')
  const model = String(modelId.value || '').trim()
  const key = String(apiKey.value || '').trim() || saved.value?.apiKey || ''
  if (!model) { error.value = '先选模型再测试'; return }
  if (!/^https?:\/\/\S+/i.test(base)) { error.value = 'Base URL 必须是 http(s) 地址'; return }
  if (!key) { error.value = '先填 API Key 再测试'; return }
  testing.value = true
  try {
    const r = await testAiConnection({ baseUrl: base, model, apiKey: key })
    if (r?.ok === false) error.value = r.why || '连接失败'
    else note.value = r?.why || '连接成功'
  } catch (e) { error.value = e?.message || '测试请求失败' }
  finally { testing.value = false }
}

async function clearConfig() {
  if (typeof window !== 'undefined' && window.confirm
      && !window.confirm('清空已保存的 AI 模型配置（厂商 / 模型 / API Key / Base URL）？')) return
  clearAiConfig()
  await pushAiConfig(null)
  saved.value = null
  emit('change', null)
  emit('close')
}

function onKey(event) { if (event.key === 'Escape') emit('close') }
onMounted(() => { if (typeof window !== 'undefined') window.addEventListener('keydown', onKey) })
onBeforeUnmount(() => { if (typeof window !== 'undefined') window.removeEventListener('keydown', onKey) })
</script>

<template>
  <Teleport to="body">
    <div class="cfg-mask" @click.self="emit('close')">
      <section class="cfg panel" role="dialog" aria-modal="true" aria-label="AI 模型配置">
        <div class="row"><b class="panel-title">AI 模型配置</b><span class="grow"></span><button class="btn btn--xs" aria-label="关闭配置" @click="emit('close')">×</button></div>
        <p class="dim tiny">选厂商 → 选模型 → 填 API Key。配置只存本机浏览器，Key 不写进项目文件、不进日志。</p>

        <div class="field">厂商
          <div class="chips">
            <button v-for="p in providers" :key="p.id" class="btn btn--xs" :class="{ 'btn--on': p.id === providerId }" :title="p.name" @click="providerId = p.id">{{ p.short || p.name }}</button>
            <button class="btn btn--xs" title="手工填写厂商名、模型 id 与 OpenAI 兼容 Base URL" @click="addCustom">＋ 自定义</button>
          </div>
        </div>

        <template v-if="isCustom">
          <label class="field">厂商名称<input class="input" v-model="customName" placeholder="例如：本地 vLLM / 企业网关" /></label>
          <label class="field">模型 id<input class="input" v-model="modelId" placeholder="例如：qwen2.5-72b-instruct" /></label>
          <label class="field">Base URL（OpenAI 兼容）<input class="input" v-model="baseUrl" placeholder="https://host/v1" /></label>
          <div class="row"><button class="btn btn--xs btn--danger" @click="dropCustom">删除该自定义厂商</button></div>
        </template>
        <template v-else>
          <div class="field">模型
            <div class="chips">
              <button v-for="m in (provider?.models || [])" :key="m.id" class="btn btn--xs" :class="{ 'btn--on': m.id === modelId }" :title="m.id" @click="modelId = m.id">{{ m.label }}</button>
            </div>
          </div>
          <p v-if="provider?.note" class="note warn">{{ provider.note }}</p>
          <label class="field">Base URL（OpenAI 兼容，可覆盖）<input class="input" v-model="baseUrl" /></label>
        </template>

        <label class="field">API Key
          <span class="keyrow">
            <input class="input" :type="showKey ? 'text' : 'password'" v-model="apiKey" autocomplete="off" spellcheck="false" placeholder="粘贴厂商控制台生成的 Key" />
            <button class="btn btn--xs" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</button>
          </span>
        </label>
        <p v-if="saved?.apiKey" class="dim tiny">本机已保存 Key：{{ maskKey(saved.apiKey) }}。重新填写即覆盖。</p>
        <p v-else-if="provider?.hint" class="dim tiny">{{ provider.hint }}</p>

        <div class="row">
          <button class="btn btn--primary" :disabled="busy" @click="save">保存</button>
          <button class="btn" :disabled="testing" @click="testConnection">{{ testing ? '测试中…' : '测试连接' }}</button>
          <span class="grow"></span>
          <button class="btn btn--danger" :disabled="!savedConfigured" @click="clearConfig">清空配置</button>
          <button class="btn" @click="emit('close')">取消</button>
        </div>
        <p v-if="error" class="note bad" role="alert">{{ error }}</p>
        <p v-if="note" class="note" role="status">{{ note }}</p>
        <p class="dim tiny">模型清单按 2026-10 各厂商公开文档核对；厂商会上下线型号，实际可调用型号以控制台为准。</p>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.cfg-mask { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center; padding: 16px; background: rgba(3, 7, 14, .62); }
.cfg { box-sizing: border-box; width: min(600px, 96vw); max-height: 88vh; overflow: auto; display: flex; flex-direction: column; gap: 10px; padding: 14px; background: var(--bg-panel, #0c1420); box-shadow: 0 18px 60px #000a; }
.cfg .row { gap: 8px; }
.field { display: flex; flex-direction: column; gap: 5px; font-size: 11.5px; color: var(--tx-2); min-width: 0; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.keyrow { display: flex; align-items: center; gap: 6px; }
.keyrow .input { flex: 1; min-width: 0; }
.cfg p { margin: 0; line-height: 1.6; overflow-wrap: anywhere; }
</style>
