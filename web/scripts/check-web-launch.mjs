import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync,existsSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import {spawnSync,execFileSync} from 'node:child_process'
const root=new URL('../../',import.meta.url)
const previous=process.env.WEB_LAUNCH_BASELINE==='1'
const read=path=>previous?execFileSync('git',['show',`HEAD:${path}`],{cwd:root,encoding:'utf8'}):readFileSync(new URL(path,root),'utf8')
const launcher=read('tools/run_backend.ps1'),bat=read('start.bat')
// 启动器真实使用的是 backend/.venv（tools\run_backend.ps1 的 $venvPy），
// 仓库根 .venv 只是旧布局残留；夹具必须用真实解释器，否则子进程起不来，
// 断言会误判成"启动器没扛住重载信号"。
const pythonCandidates=[
 resolve(fileURLToPath(root),'backend/.venv/Scripts/python.exe'),
 resolve(fileURLToPath(root),'backend/.venv/bin/python'),
 resolve(fileURLToPath(root),'.venv/Scripts/python.exe'),
 resolve(fileURLToPath(root),'.venv/bin/python'),
]
const python=process.env.WEB_LAUNCH_PYTHON||
 (pythonCandidates.find(candidate=>existsSync(candidate))||'python3')
test('Windows reload signal does not cancel the waiting launcher',t=>{
 const begin=launcher.indexOf('function Invoke-BackendProcess('),end=launcher.indexOf('\nfunction ',begin+1)
 assert.ok(begin>=0,'backend needs a console separate from the PowerShell pipeline')
 const fn=launcher.slice(begin,end),dir=mkdtempSync(join(tmpdir(),'elfin-reload-console-'))
 t.after(()=>rmSync(dir,{recursive:true,force:true}))
 const child=join(dir,'reload_fixture.py'),script=join(dir,'launch.ps1'),out=join(dir,'out.log'),error=join(dir,'error.log')
 writeFileSync(child,`import os, signal, time\nsignal.signal(signal.SIGINT, lambda *args: None)\nprint('before-reload', flush=True)\nos.kill(os.getpid(), signal.CTRL_C_EVENT)\ntime.sleep(.3)\nprint('after-reload', flush=True)\n`)
 const quote=s=>"'"+s.replaceAll("'","''")+"'"
 writeFileSync(script,`\ufeff${fn}\n$code=Invoke-BackendProcess ${quote(python)} @(${quote(child)}) ${quote(dir)} ${quote(out)} ${quote(error)}\nWrite-Output "launcher-survived:$code"\nexit $code\n`)
 const result=spawnSync('powershell.exe',['-NoProfile','-ExecutionPolicy','Bypass','-File',script],{encoding:'utf8',timeout:15000})
 assert.equal(result.status,0,result.stdout+result.stderr+readFileSync(error,'utf8'))
 assert.match(result.stdout,/launcher-survived:0/)
 assert.match(readFileSync(out,'utf8'),/after-reload/)
})
test('start/web/dev/restart share the sole hot-reload frontend and backend controller',()=>{
 for(const action of ['start','dev','restart'])assert.ok(bat.includes(`"${action}" goto hotdev`))
 // 生产网页托管已下线：web 只给出明确提示，不再伪装成可用入口。
 assert.ok(bat.includes('"web" goto webretired'))
 assert.match(bat,/:webretired\r?\n(?:echo.*\r?\n){2,}exit \/b 1/)
 assert.doesNotMatch(bat,/"web" goto hotdev/)
 const dev=read('tools/run_dev.ps1')
 assert.match(dev,/-Ensure -Reload/)
 assert.match(dev,/http:\/\/localhost:5173/)
 assert.match(dev,/Reusing/);assert.match(dev,/--strictPort/)
 assert.match(dev,/5173 is occupied by an unverified service/)
 assert.match(launcher,/\[switch\]\$Web/)
 // 仿真夹紧在真正的开发入口上：run_dev 强制 HUYAN_NO_REAL=1，启动器本身不再改环境。
 assert.match(dev,/\$env:HUYAN_NO_REAL\s*=\s*'1'/)
 assert.match(launcher,/--host', '127\.0\.0\.1'/)
 assert.match(bat,/tools\\run_dev\.ps1" -Open/)
})
test('retired production web mode fails fast with an actionable reason',()=>{
 // -Web 依赖 8710 托管 web/dist，而 backend 已按要求只保留 API；保留开关只为明确拒绝。
 assert.ok(launcher.includes('生产网页托管已下线'),'launcher must explain the retired mode')
 assert.equal(launcher.indexOf('function Test-ProductionWeb('),-1,'no page readiness validator should remain')
 assert.equal(launcher.indexOf('function Test-WebBuildIdentity('),-1,'no build identity gate should remain')
 assert.doesNotMatch(launcher,/if \(\$Web\) \{ \$launchArgs \+= '-Web' \}/)
})
test('backend API root no longer serves a page',()=>{
 const main=read('backend/main.py')
 assert.equal(main.indexOf('StaticFiles('),-1,'8710 must stay API-only; the desktop shell mounts web/dist itself')
})

