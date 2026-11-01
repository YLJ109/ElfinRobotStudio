import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {EditorState} from '@codemirror/state'
import {EditorView} from '@codemirror/view'
import postcss from 'postcss'

// Compile the production editor theme through CodeMirror itself. Its external
// tooltip parent copies theme classes but does not carry cm-editor.
test('editor height never applies to the body-level CodeMirror tooltip host',()=>{
 const source=readFileSync(new URL('../src/components/ProgramEditor.vue',import.meta.url),'utf8')
 const match=source.match(/EditorView\.theme\(([\s\S]*?), \{ dark: true \}\)/)
 assert.ok(match,'production editor theme exists')
 const spec=new Function('return ('+match[1]+')')()
 const state=EditorState.create({extensions:[EditorView.theme(spec,{dark:true})]})
 const css=state.facet(EditorView.styleModule).map(module=>module.getRules()).join('\n')
 const tree=postcss.parse(css),heightRules=[]
 tree.walkRules(rule=>{if(rule.nodes.some(decl=>decl.prop==='height'&&decl.value==='100%'))heightRules.push(rule.selector)})
 assert.ok(heightRules.length,'real editor retains a bounded full-height viewport')
 const themeClasses=new Set([...css.matchAll(/\.([^\s.#:>+~,{]+)/gu)].map(match=>match[1]).filter(name=>!name.startsWith('cm-')))
 function matchesSimple(selector,classes){
  return selector.split(',').some(part=>!/[\s:>+~]/u.test(part.trim())&&[...part.trim().matchAll(/\.([^.#:>+~\s]+)/gu)].every(match=>classes.has(match[1])))
 }
 const tooltipClasses=new Set(themeClasses),editorClasses=new Set([...themeClasses,'cm-editor'])
 assert.ok(heightRules.some(selector=>matchesSimple(selector,editorClasses)),'actual editor must keep its configured height')
 assert.ok(heightRules.every(selector=>!matchesSimple(selector,tooltipClasses)),'external tooltip host must not become a second viewport-height body child')
 assert.match(source,/tooltips\(\{ parent: document\.body, position: 'fixed' \}\)/,'unclipped point completion and diagnostic overlays remain outside scroll panels')
})
