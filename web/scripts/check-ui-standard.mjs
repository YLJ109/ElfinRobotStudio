// 仅离线DOM替身/真实CSS与模板，禁止服务或设备访问。
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {installTabNavigation} from '../src/tabNavigation.js'
import postcss from 'postcss'
const read=path=>readFileSync(new URL('../src/'+path,import.meta.url),'utf8')
function fixture(disabled=[]) {
  let handler,removed=false,clicks=[],focus=[]
  const doc={addEventListener:(name,fn)=>{handler=fn},removeEventListener:(name,fn)=>{removed=fn===handler}}
  const list={getAttribute:()=>null,querySelectorAll:()=>tabs}
  const tabs=Array.from({length:4},(_,i)=>({disabled:disabled.includes(i),tabIndex:i===0?0:-1,
    closest:selector=>selector==='[role="tab"]'?tabs[i]:list,
    focus:()=>focus.push(i),click:()=>clicks.push(i),scrollIntoView(){}}))
  const dispose=installTabNavigation(doc)
  function key(key,index=0,extra={}) {
    const event={key,target:tabs[index],preventDefault(){this.defaultPrevented=true},stopPropagation(){this.stopped=true},...extra}
    handler(event);return event
  }
  return {tabs,doc,list,key,clicks,focus,dispose,removed:()=>removed}
}
test('方向键只聚焦不切页，跳过禁用项并支持首尾',()=>{
  const f=fixture([1]);f.key('ArrowRight');assert.deepEqual(f.focus,[2]);assert.deepEqual(f.clicks,[])
  assert.equal(f.tabs[2].tabIndex,0);assert.equal(f.tabs[0].tabIndex,-1)
  f.key('End',2);f.key('Home',3);f.key('ArrowLeft',0)
  assert.deepEqual(f.focus,[2,3,0,3]);assert.deepEqual(f.clicks,[])
})
test('Enter及空格明确激活，阻止全局快捷键；其他按键不接管',()=>{
  const f=fixture();assert.equal(f.key(' ').stopped,true);f.key('Enter',2)
  assert.deepEqual(f.clicks,[0,2]);assert.equal(f.key('q').defaultPrevented,undefined)
  f.key('Enter',1,{ctrlKey:true});f.key('Enter',1,{defaultPrevented:true});f.key('Enter',1,{isComposing:true})
  assert.deepEqual(f.clicks,[0,2]);f.dispose();assert.equal(f.removed(),true)
})
test('纵向Tab按上下键；嵌套tab不混入另一分组',()=>{
  const f=fixture();f.list.getAttribute=()=> 'vertical';f.key('ArrowDown');assert.deepEqual(f.focus,[1])
  f.key('ArrowRight');assert.deepEqual(f.focus,[1]);f.tabs[2].closest=()=>({})
  f.key('ArrowDown',1);assert.deepEqual(f.focus,[1,3])
})
test('主Tab、控件副Tab和卡片样式使用同一规范且不隐藏状态副标题',()=>{
  const css=read('theme.css')
  for(const name of ['task-tabs','point-tabs','program-tabs','system-tabs','input-tabs','studio-tabs','workspace-tabs'])assert.ok(css.includes('.'+name))
  for(const token of ['--ui-card-radius','--ui-tab-radius','--ui-title-size','--ui-control-height'])assert.ok(css.includes(token))
  assert.ok(css.includes('prefers-reduced-motion'));assert.ok(css.includes('button:focus-visible'))
  assert.doesNotMatch(css,/\.panel-sub\s*\{[^}]*display:\s*none/)
  assert.doesNotMatch(read('panels/PointsPanel.vue'),/<span class="point-tab-help">/)
})

test('系统五个按钮固定同一行，不单独换行关于',()=>{
  const root=postcss.parse(read('theme.css')),rules=[]
  root.walkRules(rule=>rules.push(rule))
  const layout=rules.find(rule=>rule.selector==='.side .pane .system-tabs')
  const values=Object.fromEntries(layout.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
  assert.equal(values.display,'grid')
  assert.equal(values['grid-template-columns'],'repeat(5,minmax(0,1fr))')
  assert.equal(values['flex-wrap'],'nowrap')
  const component=read('panels/SystemPanel.vue')
  assert.deepEqual([...component.matchAll(/label: '([^']+)'/g)].map(m=>m[1]),['工具','日志','遥测','设置','关于'])
})
test('点位/程序/交互主tab都有选中态、焦点及面板关联，仍保留执行按钮',()=>{
  for(const name of ['PointsPanel','ProgramPanel','HandFollowPanel']) {
    const text=read(`panels/${name}.vue`)
    assert.ok(text.includes('role="tablist"'));assert.ok(text.includes(':tabindex='));assert.ok(text.includes('aria-controls='))
  }
  assert.ok(read('panels/PointsPanel.vue').includes('执行目标'))
})
test('页签使用直角透明底与内容基线，选中为面板底青字；操作按钮保留实色切角',()=>{
  const css=read('theme.css'),root=postcss.parse(css),rules=[]
  root.walkRules(rule=>rules.push(rule))
  const sharedSelector='.side .pane :is(.task-tabs,.point-tabs,.program-tabs,.system-tabs,.input-tabs,.studio-tabs,.workspace-tabs)'
  const unified=rules.find(rule=>rule.selectors.includes(sharedSelector))
  const values=Object.fromEntries(unified.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
  assert.equal(values.border,'0');assert.equal(values.background,'transparent')
  assert.equal(values['border-bottom'],'1px solid var(--line-2)')
  assert.equal(values.padding,'0')
  const declarations=rule=>Object.fromEntries(rule.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
  const tab=declarations(rules.find(rule=>rule.selectors.includes(sharedSelector+' > button')))
  assert.equal(tab.background,'transparent');assert.equal(tab.color,'var(--tx-2)')
  assert.equal(tab.border,'1px solid transparent');assert.equal(tab['border-radius'],'0')
  assert.equal(tab['clip-path'],'none');assert.equal(tab['margin-bottom'],'-1px')
  const primary=rules.find(rule=>rule.selector.includes('> button:is([aria-selected="true"]'))
  const selected=declarations(primary)
  assert.equal(selected.background,'var(--bg-raise)');assert.equal(selected.color,'var(--acc)')
  assert.equal(selected['border-bottom-color'],selected.background,'selected tab opens onto the content baseline')
  assert.equal(selected['box-shadow'],'none')
  const hover=rules.find(rule=>rule.selectors.includes(sharedSelector+' > button:where(:hover):not(:disabled)'))
  assert.ok(hover,'hover has no extra specificity over selected state')
  assert.ok(rules.indexOf(hover)<rules.indexOf(primary),'selected styling wins when hovered')
  assert.equal(declarations(hover).background,'var(--bg-panel)')
  const focus=declarations(rules.find(rule=>rule.selectors.includes(sharedSelector+' > button:focus-visible')))
  assert.equal(focus.outline,'2px solid var(--acc)');assert.equal(focus['outline-offset'],'-2px')
  const disabled=declarations(rules.find(rule=>rule.selectors.includes(sharedSelector+' > button:disabled')))
  assert.equal(disabled.cursor,'not-allowed');assert.equal(disabled.opacity,'.4')
  const action=declarations(rules.find(rule=>rule.selector==='button.btn--primary, button.btn--on'))
  assert.equal(action.background,'var(--acc)');assert.equal(action.color,'#04131a')
  const actionShape=declarations(rules.find(rule=>rule.selector.startsWith('button.btn, .side .pane button,')))
  assert.equal(actionShape['clip-path'],'var(--clip-br)');assert.equal(actionShape['border-radius'],'0')
  const theme=css.slice(css.indexOf('/* 工作区统一规范'))
  assert.doesNotMatch(theme,/border-radius:\s*[1-9]/,'新统一层不得重新引入圆角')
})
test('只有语义为tablist的seg共享完整页签状态，普通分段控件保留操作样式',()=>{
  const root=postcss.parse(read('theme.css')),rules=[]
  root.walkRules(rule=>rules.push(rule))
  const shared='.side .pane :is(.task-tabs,.point-tabs,.program-tabs,.system-tabs,.input-tabs,.studio-tabs,.workspace-tabs)'
  const inner='.side .pane .seg[role="tablist"]'
  const sharedRules=rules.filter(rule=>rule.selectors.some(selector=>selector.startsWith(shared)))
  assert.equal(sharedRules.length,6,'layout, inactive, hover, selected, disabled and focus')
  for(const rule of sharedRules) {
    const selector=rule.selectors.find(selector=>selector.startsWith(shared))
    assert.ok(rule.selectors.includes(selector.replace(shared,inner)),`inner tabs must share ${selector}`)
    assert.equal(rule.selectors.some(selector=>selector.startsWith('.side .pane .seg >')),false)
  }
  const values=rule=>Object.fromEntries(rule.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
  const compact=values(rules.find(rule=>rule.selectors.includes(inner+' > button')&&rule.selectors.includes('.side .system-content .task-tabs > button')))
  assert.equal(compact['min-height'],'28px');assert.equal(compact.padding,'4px 6px')
  const ordinary=values(rules.find(rule=>rule.selector==='.side .pane .seg > button'))
  assert.equal(ordinary['clip-path'],'var(--clip-br)');assert.equal(ordinary.background,'var(--bg-raise)')
  const selected=values(rules.find(rule=>rule.selector==='.side .pane .seg > button.on'))
  assert.equal(selected.background,'var(--acc)');assert.equal(selected.color,'#04131a')
})
test('工艺ipt与AI裸文本域都纳入主题，原生滑条/复选框不被当文本框',()=>{
  const root=postcss.parse(read('theme.css')),forms=[]
  root.walkRules(rule=>{if(rule.selector.includes('input:where(:not([type="checkbox"])'))forms.push(rule)})
  assert.equal(forms.length,1)
  const rule=forms[0],values=Object.fromEntries(rule.nodes.filter(n=>n.type==='decl').map(n=>[n.prop,n.value]))
  assert.ok(rule.selector.includes('.ipt'));assert.ok(rule.selector.includes('textarea, select'))
  assert.ok(rule.selector.includes(':not([type="range"])'));assert.ok(rule.selector.includes(':not([type="file"])'))
  assert.equal(values['background-color'],'var(--bg-inset)');assert.equal(values.color,'var(--tx-1)')
  assert.equal(values['border-radius'],'0');assert.equal(values['color-scheme'],'dark')
})
