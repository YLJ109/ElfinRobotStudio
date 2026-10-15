<script setup>
import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import { Compartment, EditorState, Prec } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, tooltips, Decoration, WidgetType, ViewPlugin } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { autocompletion, acceptCompletion, closeCompletion, startCompletion, closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { StreamLanguage, HighlightStyle, syntaxHighlighting, indentUnit, bracketMatching } from '@codemirror/language'
import { tags } from '@lezer/highlight'
import { PROGRAM_COMMANDS, TOOL_ACTIONS, PYTHON_WORDS, CONTAINER_METHODS, matchingCommands } from '../programLanguage'

const props = defineProps({ modelValue: { type: String, default: '' }, language: { type: String, default: 'zh' }, points: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue', 'preview-point', 'select-point'])
const pointReferences = computed(() => new Map(props.points.flatMap(p => [[p.code, p], [p.name, p]])))
const host = ref(null)
let view = null
const completions = new Compartment()
const pointWidgets = new Compartment()
const keywords = new Set([...PROGRAM_COMMANDS, ...TOOL_ACTIONS, ...PYTHON_WORDS].flatMap(c => [c.en.toUpperCase(), c.zh]))
const language = StreamLanguage.define({
  startState: () => ({ indent: 0 }),
  indent: state => state.indent,
  blankLine: state => { state.indent = Math.max(0, state.indent) },
  token(stream, state) {
    if (stream.sol()) state.indent = stream.indentation() + (/[:：]\s*(?:#.*)?$/.test(stream.string) ? 4 : 0)
    if (stream.eatSpace()) return null
    if (stream.match('#')) { stream.skipToEnd(); return 'comment' }
    if (stream.match(/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/)) return 'string'
    if (stream.match(/\$\{[^}]+\}/)) return 'variableName'
    if (stream.match(/[+-]?\d+(?:\.\d+)?/)) return 'number'
    if (stream.match(/[\w\u3400-\u9fff]+/)) return keywords.has(stream.current().toUpperCase()) ? 'keyword' : null
    stream.next()
    return null
  },
})
const angles = p => p.joints?.map((v, i) => `J${i + 1}=${Number(v).toFixed(3)}°`).join(' · ') || '无关节数据'
const poseText = p => [...(p.tcp?.xyz_mm || []), ...(p.tcp?.rpy_deg || [])].map((v, i) => `${['X','Y','Z','A','B','C'][i]}=${Number(v).toFixed(3)}${i < 3 ? 'mm' : '°'}`).join(' · ')
class GhostWidget extends WidgetType {
  constructor(point) { super(); this.point = point }
  eq(other) { return JSON.stringify(this.point) === JSON.stringify(other.point) }
  toDOM() {
    const button = document.createElement('button')
    button.type = 'button'; button.className = 'point-ghost-button'; button.textContent = '影'
    button.title = `${this.point.code || ''} · ${this.point.name}\n${angles(this.point)}\n${poseText(this.point)}`
    button.setAttribute('aria-label', `预览点位 ${this.point.code || this.point.name} 残影`)
    button.addEventListener('mousedown', e => e.preventDefault())
    button.addEventListener('click', () => emit('preview-point', this.point))
    return button
  }
  ignoreEvent() { return true }
}
function pointExtension() {
  return ViewPlugin.fromClass(class {
    constructor(editor) { this.decorations = this.build(editor) }
    update(update) { if (update.docChanged || update.viewportChanged) this.decorations = this.build(update.view) }
    build(editor) {
      const ranges = []
      for (const { from, to } of editor.visibleRanges) {
        for (let pos = editor.state.doc.lineAt(from).from; pos <= to;) {
          const line = editor.state.doc.lineAt(pos)
          const text = line.text.replace(/#.*$/, '')
          const tokens = /"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[\w\u3400-\u9fff]+/g
          let match
          while ((match = tokens.exec(text))) {
            let ref = match[0].replace(/^["']|["']$/g, '').replace(/\\(['"\\])/g, '$1')
            if (match[0].startsWith('"')) { try { ref = JSON.parse(match[0]) } catch {} }
            const point = pointReferences.value.get(ref)
            if (point) ranges.push(Decoration.widget({ widget: new GhostWidget(point), side: 1 }).range(line.from + match.index + match[0].length))
          }
          if (line.to === editor.state.doc.length) break
          pos = line.to + 1
        }
      }
      return Decoration.set(ranges, true)
    }
  }, { decorations: plugin => plugin.decorations })
}
function completionSource(context) {
  const line = context.state.doc.lineAt(context.pos)
  const before = line.text.slice(0, context.pos - line.from)
  const command = before.match(/^\s*([\w\u3400-\u9fff]*)$/)
  const action = before.match(/^\s*(?:TOOL|工具)(?:\s+|\(\s*)["']?([\w\u3400-\u9fff]*)$/i)
  const method = before.match(/[\w\u3400-\u9fff]+\.([\w\u3400-\u9fff]*)$/)
  const pointArg = before.match(/(?:MOVEJ|MOVEL|关节移动|关节运动|直线移动|直线运动)\s*(?:\(\s*)?(?:"([^"\n]*)|'([^'\n]*)|([\w\u3400-\u9fff]*))$/i)
  if (pointArg && !command) {
    const prefix = (pointArg[1] ?? pointArg[2] ?? pointArg[3]).toLowerCase()
    const quoted = /["'][^"']*$/.test(before)
    return { from: context.pos - prefix.length, options: props.points.filter(p => [p.code, p.name].some(v => v?.toLowerCase().startsWith(prefix))).map(p => ({
      label: p.code || p.name, displayLabel: `${p.code || ''} · ${p.name}`, type: 'variable', detail: `(${p.joints?.map(v => Number(v).toFixed(3)).join(', ')})`,
      info: () => { const el = document.createElement('div'); el.className = 'point-completion-info'; el.textContent = `${p.code} · ${p.name}\n${angles(p)}\n${poseText(p)}\n${p.source === 'real' ? '真机保存' : '仿真保存'} · TCP 来源：正解`; return el },
      apply: (editor, completion, from, to) => { const ref = quoted ? (pointArg[1] !== undefined ? JSON.stringify(p.name).slice(1,-1) : p.name.replace(/\\/g,'\\\\').replace(/'/g,"\\'")) : p.code || p.name; const trailing = context.state.doc.sliceString(to, line.to).match(/^[\w\u3400-\u9fff]*/)?.[0] || ''; editor.dispatch({ changes: { from, to: to + trailing.length, insert: ref }, selection: { anchor: from + ref.length } }); emit('select-point', p) },
    })), filter: false }
  }
  const general = before.match(/([a-zA-Z\u3400-\u9fff][\w\u3400-\u9fff]*)$/)
  if (!command && !action && !method && (!general || /#|["']/.test(before.slice(0, -general[1].length)))) return null
  if (method && /#|["']/.test(before)) return null
  const prefix = (command || action || method || general)[1]
  if (!prefix && !context.explicit) return null
  const options = matchingCommands(action ? TOOL_ACTIONS : method ? CONTAINER_METHODS : command ? [...PROGRAM_COMMANDS.filter(c => !['LOOP','END','IF_DI','LABEL'].includes(c.en)), ...PYTHON_WORDS] : PYTHON_WORDS.filter(c => !c.snippet), prefix).map(item => ({
    label: props.language === 'zh' ? item.zh : item.en,
    type: 'keyword', detail: (props.language === 'zh' ? item.args : item.enArgs || item.args) || '',
    apply: (editor, completion, from, to) => {
      const next = editor.state.doc.sliceString(to, to + 1)
      const callable = !action && !/^\s|\(/.test(next) && (item.callable || !item.snippet && PROGRAM_COMMANDS.some(c => c.en === item.en) && !['IF_DI', 'LABEL', 'END'].includes(item.en))
      const argText = props.language === 'zh' ? item.args : item.enArgs || item.args
      const text = callable ? completion.label + '()' : item.snippet ? completion.label + (argText === ':' ? ':' : ' ' + argText) + '\n' + ' '.repeat(line.text.match(/^\s*/)[0].length + 4) : completion.label + (!next.match(/\s/) && item.args ? ' ' : '')
      editor.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length - (callable ? 1 : 0) }, userEvent: 'input.complete' })
    },
  }))
  if (!action && !method) {
    const symbols = new Map()
    for (const match of context.state.doc.toString().matchAll(/^\s*(?:def|定义)\s+([\w\u3400-\u9fff]+)\(([^)]*)\)|^\s*([a-zA-Z\u3400-\u9fff][\w\u3400-\u9fff]*)\s*=/gm)) {
      const name = match[1] || match[3]
      if (!name.toLowerCase().startsWith(prefix.toLowerCase())) continue
      symbols.set(name, {label:name,type:match[1] ? 'function' : 'variable',detail:match[2] || '', apply: (editor, completion, from, to) => { const call = !!match[1] && context.state.doc.sliceString(to,to+1) !== '('; const text = name + (call ? '()' : ''); editor.dispatch({changes:{from,to,insert:text},selection:{anchor:from+text.length-(call?1:0)}}) }})
    }
    options.push(...symbols.values())
  }
  const suffix = line.text.slice(context.pos - line.from).match(/^[\w\u3400-\u9fff]*/)?.[0] || ''
  return { from: context.pos - prefix.length, to: context.pos + suffix.length, options, filter: false }
}
function completionExtension() {
  return [
    EditorState.phrases.of(props.language === 'zh' ? { 'Completions': '补全指令' } : {}),
    autocompletion({ override: [completionSource], activateOnTypingDelay: 80, interactionDelay: 0,
      aboveCursor: false, icons: false, maxRenderedOptions: 13, optionClass: item => item.type === 'variable' ? 'point-option' : '', tooltipClass: () => 'program-completions' }),
  ]
}
onMounted(() => {
  view = new EditorView({ parent: host.value, state: EditorState.create({ doc: props.modelValue, extensions: [
    lineNumbers(), highlightActiveLine(), history(), language, indentUnit.of('    '), bracketMatching(), closeBrackets(), pointWidgets.of(pointExtension()),
    syntaxHighlighting(HighlightStyle.define([
      { tag: tags.keyword, color: '#4bdcf2' }, { tag: tags.number, color: '#ffd166' },
      { tag: tags.comment, color: '#8798b5' }, { tag: tags.variableName, color: '#b49bff' },
      { tag: tags.string, color: '#8cddb0' },
    ])),
    tooltips({ parent: document.body, position: 'fixed' }),
    completions.of(completionExtension()),
    Prec.highest(keymap.of([{ key: 'Tab', run: acceptCompletion }])),
    keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
    EditorView.contentAttributes.of({ 'aria-label': '程序代码', spellcheck: 'false' }),
    EditorView.updateListener.of(update => { if (update.docChanged) emit('update:modelValue', update.state.doc.toString()) }),
    EditorView.theme({
      '&': { height: '100%', color: 'var(--tx-1)', backgroundColor: 'var(--bg-inset)', fontSize: '13px' },
      '&.cm-focused': { outline: 'none', boxShadow: 'inset 0 0 0 1px var(--acc)' },
      '.cm-scroller': { fontFamily: 'Consolas, "Microsoft YaHei UI", monospace', lineHeight: '1.7', overflow: 'auto' },
      '.cm-content': { padding: '8px 0', caretColor: 'var(--acc)' },
      '.cm-line': { padding: '0 8px' }, '.cm-cursor': { borderLeftColor: 'var(--acc)' },
      '.cm-gutters': { backgroundColor: 'var(--bg-inset)', color: 'var(--tx-3)', borderRight: '1px solid var(--line)' },
      '.cm-activeLine': { backgroundColor: 'rgba(47,214,240,.05)' },
      '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'rgba(47,214,240,.2)' },
    }, { dark: true }),
  ] }) })
})
watch(() => props.modelValue, value => {
  if (view && value !== view.state.doc.toString()) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
})
watch(() => props.language, () => {
  if (!view) return
  closeCompletion(view)
  view.dispatch({ effects: completions.reconfigure(completionExtension()) })
  if (view.hasFocus) startCompletion(view)
})
watch(() => props.points, () => { if (view) view.dispatch({ effects: pointWidgets.reconfigure(pointExtension()) }) }, { deep: true })
onBeforeUnmount(() => { view?.destroy(); view = null })
</script>

<template><div ref="host" class="program-editor"></div></template>

<style scoped>
.program-editor { flex: 1; min-height: 120px; overflow: hidden; border: 1px solid var(--line-2); border-radius: 5px; }
</style>
<style>
.point-ghost-button { margin: 0 4px; padding: 0 4px; line-height: 18px; border: 1px solid #266975; border-radius: 3px; color: #4bdcf2; background: #102630; cursor: pointer; font: 11px "Microsoft YaHei UI", sans-serif; vertical-align: middle; }
.point-completion-info { white-space: pre-wrap; line-height: 1.7; padding: 8px; max-width: 310px; font: 11px var(--font-mono); }
.cm-tooltip.cm-completionInfo { background: var(--bg-panel); color: var(--tx-1); border: 1px solid var(--line-3); z-index: 41; }
</style>
<style>
.cm-tooltip.program-completions { z-index: 40; border: 1px solid var(--line-3); border-radius: 6px; background: var(--bg-panel);
  color: var(--tx-1); box-shadow: var(--sh-2); font: 12px var(--font-body); overflow: hidden; }
.cm-tooltip.program-completions ul { max-width: min(380px, calc(100vw - 20px)); max-height: 230px; }
.cm-tooltip.program-completions ul li { padding: 5px 8px; display: flex; align-items: center; gap: 12px; }
.cm-tooltip.program-completions ul li[aria-selected] { background: var(--acc-soft); color: var(--acc); }
.cm-tooltip.program-completions .cm-completionDetail { margin-left: auto; color: var(--tx-3); font-size: 11px; font-style: normal; }
.cm-tooltip.program-completions ul li.point-option { flex-wrap: wrap; gap: 2px; }
.cm-tooltip.program-completions .point-option .cm-completionDetail { display: block; width: 100%; white-space: normal; overflow-wrap: anywhere; margin: 0; }
</style>
