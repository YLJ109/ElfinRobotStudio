// AI 助手的「厂商 / 模型 / API Key」配置：目录 + 本机 localStorage 存取 + 后端同步。
//
// 模型 id 全部按 2026-10 各厂商公开文档核对（见 MODEL_SOURCES），只列 OpenAI 兼容
// /chat/completions 可调用的**文本**模型。厂商随时上下线型号，这里存的是当时核对到的
// 真实 id；自定义厂商条目允许手填，避免目录过期就完全用不了。

export const AI_CONFIG_KEY = 'huayan.ai.config'
export const AI_CUSTOM_KEY = 'huayan.ai.providers'
export const CUSTOM_PREFIX = 'custom:'
export const DEFAULT_PROVIDER_ID = 'zhipu'

export const MODEL_SOURCES = [
  ['智谱', 'https://docs.bigmodel.cn/api-reference/模型-api/对话补全'],
  ['阿里通义千问', 'https://help.aliyun.com/zh/document_detail/3016808.html'],
  ['百度千帆', 'https://qianfan.cloud.baidu.com/pricing'],
  ['月之暗面 Kimi', 'https://platform.kimi.ai/docs/models'],
  ['深度求索 DeepSeek', 'https://api-docs.deepseek.com/quick_start/pricing'],
  ['科大讯飞 Spark', 'https://www.xfyun.cn/doc/spark/HTTP调用文档.html'],
  ['腾讯混元', 'https://cloud.tencent.com/document/product/1729'],
  ['火山方舟豆包', 'https://ark.volcengine.com/docs/兼容 OpenAI SDK'],
  ['MiniMax', 'https://platform.minimax.io'],
  ['零一万物 Yi', 'https://platform.lingyiwanwu.com/docs'],
]

export const PROVIDERS = [
  {
    id: 'zhipu', name: '智谱 Zhipu', short: '智谱',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    hint: '开放平台控制台生成 API Key',
    models: [
      { id: 'glm-5.3', label: 'glm-5.3 · 最新旗舰' },
      { id: 'glm-5.2', label: 'glm-5.2 · 旗舰开源权重' },
      { id: 'glm-4.7', label: 'glm-4.7 · 均衡旗舰' },
      { id: 'glm-4.6', label: 'glm-4.6 · 200K 上下文' },
      { id: 'glm-4.7-flashx', label: 'glm-4.7-flashx · 高速低价' },
      { id: 'glm-4.5-air', label: 'glm-4.5-air · 高性价比' },
      { id: 'glm-4.5-flash', label: 'glm-4.5-flash · 免费档' },
      { id: 'glm-4-flash-250414', label: 'glm-4-flash-250414 · 免费档' },
    ],
  },
  {
    id: 'qwen', name: '阿里通义千问 DashScope', short: '通义千问',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    hint: '百炼控制台（华北2 北京）API Key；国际站换 dashscope-intl 域名',
    models: [
      { id: 'qwen3.8-max', label: 'qwen3.8-max · 最新旗舰' },
      { id: 'qwen3.7-max', label: 'qwen3.7-max · 旗舰' },
      { id: 'qwen3.7-plus', label: 'qwen3.7-plus · 性价比' },
      { id: 'qwen3.7-flash', label: 'qwen3.7-flash · 高速低价' },
      { id: 'qwen3.5-plus', label: 'qwen3.5-plus · 稳定版' },
    ],
  },
  {
    id: 'ernie', name: '百度千帆 Ernie', short: '文心千帆',
    baseUrl: 'https://qianfan.baidubce.com/v2',
    hint: '千帆「应用接入」生成的 API Key（形如 bce-v3-…）',
    models: [
      { id: 'ernie-5.1', label: 'ernie-5.1 · 最新旗舰' },
      { id: 'ernie-5.0', label: 'ernie-5.0 · 旗舰' },
      { id: 'ernie-5.0-thinking-preview', label: 'ernie-5.0-thinking-preview · 深度推理' },
      { id: 'ernie-x1.1-preview', label: 'ernie-x1.1-preview · 推理' },
      { id: 'ernie-4.5-turbo-128k', label: 'ernie-4.5-turbo-128k · 性价比长文' },
    ],
  },
  {
    id: 'kimi', name: '月之暗面 Kimi Moonshot', short: 'Kimi',
    baseUrl: 'https://api.moonshot.cn/v1',
    hint: 'Kimi 开放平台 API Key；文档另给国际站 https://api.moonshot.ai/v1',
    note: 'K2 系列 2026-12 底下线，moonshot-v1 系列已下线，勿选。',
    models: [
      { id: 'kimi-k3.1-pro', label: 'kimi-k3.1-pro · 最新旗舰' },
      { id: 'kimi-k3.1-pro-highspeed', label: 'kimi-k3.1-pro-highspeed · 高速' },
      { id: 'kimi-k3', label: 'kimi-k3 · 上代旗舰' },
      { id: 'kimi-k2.7-code', label: 'kimi-k2.7-code · 编码' },
      { id: 'kimi-k2.6', label: 'kimi-k2.6 · 通用 256K' },
    ],
  },
  {
    id: 'deepseek', name: '深度求索 DeepSeek', short: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    hint: 'DeepSeek 开放平台 API Key',
    note: 'deepseek-chat / deepseek-reasoner 两个旧名已于 2026-07-24 弃用，改用 V4 系列。',
    models: [
      { id: 'deepseek-v4-pro', label: 'deepseek-v4-pro · 旗舰' },
      { id: 'deepseek-v4-flash', label: 'deepseek-v4-flash · 高性价比' },
    ],
  },
  {
    id: 'spark', name: '科大讯飞 Spark', short: '讯飞星火',
    baseUrl: 'https://spark-api-open.xf-yun.com/v1',
    hint: '填控制台的 APIPassword，不是 APIKey',
    models: [
      { id: '4.0Ultra', label: '4.0Ultra · 旗舰 32K' },
      { id: 'generalv3.5', label: 'generalv3.5 · Max' },
      { id: 'generalv3', label: 'generalv3 · Pro' },
      { id: 'pro-128k', label: 'pro-128k · 长文本' },
      { id: 'max-32k', label: 'max-32k · Max-32K' },
      { id: 'lite', label: 'lite · 轻量免费' },
    ],
  },
  {
    id: 'hunyuan', name: '腾讯混元 Hunyuan', short: '混元',
    baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    hint: '腾讯云混元控制台 API Key',
    models: [
      { id: 'hunyuan-turbos-latest', label: 'hunyuan-turbos-latest · 旗舰' },
      { id: 'hunyuan-t1-latest', label: 'hunyuan-t1-latest · 深度推理' },
      { id: 'hunyuan-lite', label: 'hunyuan-lite · 轻量' },
    ],
  },
  {
    id: 'ark', name: '字节豆包 火山方舟 Ark', short: '豆包方舟',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    hint: '方舟控制台 API Key；模型 id 需在控制台先开通对应接入点',
    models: [
      { id: 'doubao-seed-2-1-pro-260915', label: 'doubao-seed-2-1-pro-260915 · 旗舰' },
      { id: 'doubao-seed-2-1-lite-260915', label: 'doubao-seed-2-1-lite-260915 · 性价比' },
      { id: 'doubao-seed-2-1-turbo-260628', label: 'doubao-seed-2-1-turbo-260628 · 高速' },
      { id: 'doubao-seed-evolving', label: 'doubao-seed-evolving · Coding/Agent' },
    ],
  },
  {
    id: 'minimax', name: 'MiniMax', short: 'MiniMax',
    baseUrl: 'https://api.minimaxi.com/v1',
    hint: 'MiniMax 开放平台 API Key；国内站可用 https://api.minimax.chat/v1',
    note: 'M2.5 / M2.7 系列 2026-11-18 下线，优先用 M3 系列。',
    models: [
      { id: 'MiniMax-M3', label: 'MiniMax-M3 · 旗舰 1M' },
      { id: 'MiniMax-M3.1-Flash-Preview', label: 'MiniMax-M3.1-Flash-Preview · 高速预览' },
      { id: 'MiniMax-M2.7', label: 'MiniMax-M2.7 · 即将下线' },
    ],
  },
  {
    id: 'yi', name: '零一万物 Yi', short: '零一万物',
    baseUrl: 'https://api.lingyiwanwu.com/v1',
    hint: '零一万物开放平台 API Key',
    models: [
      { id: 'yi-lightning', label: 'yi-lightning · 旗舰高性价比' },
      { id: 'yi-large', label: 'yi-large · 旗舰' },
      { id: 'yi-large-turbo', label: 'yi-large-turbo · 高速' },
      { id: 'yi-medium', label: 'yi-medium · 中量级' },
      { id: 'yi-medium-200k', label: 'yi-medium-200k · 长文本' },
      { id: 'yi-spark', label: 'yi-spark · 轻量' },
    ],
  },
]

function safeParse(raw, fallback) {
  if (!raw) return fallback
  try {
    const value = JSON.parse(raw)
    return value && typeof value === 'object' ? value : fallback
  } catch { return fallback }
}

function readRawList() {
  if (typeof localStorage === 'undefined') return []
  const list = safeParse(localStorage.getItem(AI_CUSTOM_KEY), [])
  return Array.isArray(list) ? list.filter((p) => p && typeof p.id === 'string' && p.id.startsWith(CUSTOM_PREFIX)) : []
}

/** 内置厂商 + 本机保存过的自定义厂商。 */
export function allProviders() {
  return [...PROVIDERS, ...readRawList()]
}

export function providerById(id) {
  return allProviders().find((p) => p.id === id) || null
}

export function saveCustomProvider(entry) {
  const list = readRawList().filter((p) => p.id !== entry.id)
  list.push(entry)
  if (typeof localStorage !== 'undefined') localStorage.setItem(AI_CUSTOM_KEY, JSON.stringify(list))
}

export function removeCustomProvider(id) {
  if (!id.startsWith(CUSTOM_PREFIX)) return
  const list = readRawList().filter((p) => p.id !== id)
  if (typeof localStorage !== 'undefined') localStorage.setItem(AI_CUSTOM_KEY, JSON.stringify(list))
}

export function readAiConfig() {
  if (typeof localStorage === 'undefined') return null
  const raw = safeParse(localStorage.getItem(AI_CONFIG_KEY), null)
  if (!raw || typeof raw.model !== 'string' || !raw.model) return null
  return {
    providerId: typeof raw.providerId === 'string' ? raw.providerId : '',
    providerName: typeof raw.providerName === 'string' ? raw.providerName : '',
    model: raw.model,
    baseUrl: typeof raw.baseUrl === 'string' ? raw.baseUrl : '',
    apiKey: typeof raw.apiKey === 'string' ? raw.apiKey : '',
    savedAt: typeof raw.savedAt === 'number' ? raw.savedAt : 0,
  }
}

export function writeAiConfig(config) {
  const next = {
    providerId: String(config.providerId || ''),
    providerName: String(config.providerName || ''),
    model: String(config.model || ''),
    baseUrl: String(config.baseUrl || ''),
    apiKey: String(config.apiKey || ''),
    savedAt: Date.now(),
  }
  if (typeof localStorage !== 'undefined') localStorage.setItem(AI_CONFIG_KEY, JSON.stringify(next))
  return next
}

export function clearAiConfig() {
  if (typeof localStorage !== 'undefined') localStorage.removeItem(AI_CONFIG_KEY)
}

/** 只显示后 4 位；Key 本体不进界面文本、不进日志。 */
export function maskKey(key) {
  const text = String(key || '')
  if (!text) return ''
  return text.length <= 4 ? '••••' : '••••' + text.slice(-4)
}

export function isConfigured(config = readAiConfig()) {
  return !!(config && config.model && config.baseUrl && config.apiKey)
}

async function postAi(path, body) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
  })
  if (!r.ok) return { ok: false, why: `HTTP ${r.status}` }
  return await r.json()
}

/** 把本机配置交给后端进程内存（后端不落盘、不记 Key）。 */
export function pushAiConfig(config = readAiConfig()) {
  if (!config || !isConfigured(config)) return postAi('/api/ai/config', { clear: true })
  return postAi('/api/ai/config', {
    provider: config.providerName || config.providerId,
    model: config.model,
    base_url: config.baseUrl,
    api_key: config.apiKey,
  })
}

/** 真的发一条极短对话验证 Key/模型/地址；后端如实返回，前端不做推测。 */
export function testAiConnection({ baseUrl, model, apiKey }) {
  return postAi('/api/ai/test', { base_url: baseUrl, model, api_key: apiKey })
}
