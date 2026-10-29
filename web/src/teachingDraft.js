import { ref } from 'vue'
export { targetContext as teachingContext } from './targetState.js'

export const TEACHING_DRAFT_KEY = 'elfin.teaching.draft.v1'
export const DEFAULT_TEACHING_POSE = Object.freeze([0, -400, 700, 90, 0, 0])
export const MAX_TEACHING_ROWS = 100
export const validTeachingVector = value => Array.isArray(value) && value.length === 6 && value.every(Number.isFinite)
export const copyTeachingRows = rows => rows.map(row => ({ name: row.name, joints: [...row.joints] }))

export function validTeachingRows(rows, named = false) {
  return Array.isArray(rows) && rows.length <= MAX_TEACHING_ROWS && rows.every(row => row &&
    typeof row.name === 'string' && row.name.length <= 80 && (!named || row.name.trim().length > 0) && validTeachingVector(row.joints))
}

// Whitelist only: never persist solution validity, permissions, history or busy.
export function teachingDraftValue({ pose, label, rows, saveUnconfirmed = false }) {
  if (!validTeachingRows(rows)) throw new Error('草稿包含无效点位，尚未写入本地')
  return { version: 1, pose: validTeachingVector(pose) ? [...pose] : [...DEFAULT_TEACHING_POSE],
    label: String(label).slice(0, 60), rows: copyTeachingRows(rows), saveUnconfirmed: saveUnconfirmed === true }
}

export function readTeachingDraft(storage) {
  const raw = storage.getItem(TEACHING_DRAFT_KEY)
  if (raw === null) return null
  if (raw.length > 200_000) throw new Error('本地草稿过大；原内容未覆盖')
  let value
  try { value = JSON.parse(raw) } catch { throw new Error('本地草稿损坏；原内容未覆盖') }
  if (!value || value.version !== 1 || !validTeachingVector(value.pose) || typeof value.label !== 'string'
      || value.label.length > 60 || !validTeachingRows(value.rows)) throw new Error('本地草稿格式无效；原内容未覆盖')
  return teachingDraftValue(value)
}

// Shared across mounts using the same store; navigation cannot free live HTTP.
const flights = new WeakMap()
export function teachingFlight(world) {
  if (!flights.has(world)) {
    const kind = ref('')
    flights.set(world, { kind, acquire(next) {
      if (kind.value) return null
      kind.value = next
      let released = false
      return () => { if (!released) { released = true; kind.value = '' } }
    } })
  }
  return flights.get(world)
}
