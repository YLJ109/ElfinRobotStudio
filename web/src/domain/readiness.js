// Pure display projection; never grants permission or sends a command.
export function taskProjection(s,fresh){
 if(!fresh||!s)return {owner:'状态未知',phase:'等待有效状态',count:'',next:'恢复连接并等待新反馈',stop:'unknown'}
 const r=s.exec||{},e=r.execution||{},active=['running','waiting','paused'].includes(r.state),h=s.mode==='real'?s.real_hand_follow:s.hand_follow
 let owner='无活动任务',phase='空闲',next='选择任务并检查启动条件',count=''
 if(s.free?.on){owner='手拖示教';phase='手拖中';next='退出手拖并确认反馈'}
 else if(h?.active||h?.phase==='stopping'){owner='交互跟随';phase=h.status||h.phase||'跟随中';next=h.blocked_reason||h.why||'保持有效输入，结束时停止'}
 else if(active){owner='程序任务';phase={running:'执行中',waiting:'等待反馈',paused:'已暂停'}[r.state];next=r.error||'等待当前步骤确认'}
 else if(e.busy){owner='运动指令';phase=e.action||'执行中';next='等待到位反馈'}
 else if(s.robot_play?.planning){owner='取放路径预检';phase=s.robot_play.progress?.phase==='cancelling'?'正在取消预检':'预检中 · 尚未运动';next='等待完整预检，或取消本局';const p=s.robot_play.progress;if(Number.isInteger(p?.step)&&Number.isInteger(p?.total))count=`已检查 ${p.step}/${p.total} 步`}
 else if(s.moving){owner='运动中 · 来源未报告';phase='等待反馈';next='确认运动来源与停止状态'}
 else if(['finished','aborted','error'].includes(r.state)){phase={finished:'程序已结束',aborted:'程序已停止',error:'程序失败'}[r.state];next=r.error||'查看运行记录'}
 if(Number.isInteger(r.completed_index)&&r.completed_index>=0&&(active||['finished','aborted','error'].includes(r.state))){count=!r.dynamic&&Number.isInteger(r.total)&&r.total>=r.completed_index?`已确认 ${r.completed_index}/${r.total} 步`:`已确认 ${r.completed_index} 步`;if(Number.isInteger(r.current?.line)&&r.current.line>0)phase+=` · 第${r.current.line}行`}
 const stop=r.stop_result
 if(stop&&stop.settled!==true){phase='停止待确认';next=stop.why||'等待新反馈证明停稳，保持运动闭锁'}
 return {owner,phase,count,next,stop:stop?(stop.settled===true?'settled':'pending'):null}
}
export function readinessProjection(s,fresh){
 const row=(id,label,ok,reason,next,done,view,systemTab)=>({id,label,state:ok===true?'ready':ok===false?'blocked':'unknown',reason,next,done,view,systemTab})
 if(!fresh||!s)return [row('connection','连接与反馈',null,'状态已过期','检查服务和连接，等待新状态','收到当前连接的有效状态','engineering','tele')]
 const real=s.mode==='real',r=s.real||{},m=s.tools?.mounted||{},q=r.controller_info||{}
 const rows=[row('connection','连接',!real||r.cmd_up===true,real?(r.cmd_up?'命令通道已连接':'命令通道未连接'):'仿真，不连接设备','检查连接与服务','当前命令通道连接有效','power'),row('feedback','反馈',real?r.stream_up===true&&r.quality==='valid':s.joints?.known===true,real?(r.quality==='valid'?'当前反馈有效':'反馈缺失或过期'):'仿真姿态，不代表现场反馈','检查当前反馈与姿态','当前连接收到完整有效姿态','engineering','tele'),row('tool','工具',!real||['manual_confirmed','detected_verified'].includes(m.state),real?(m.state||'尚未确认当前工具'):'仿真工具，不代表现场资格','核对安装工具、工具中心点和负载','当前工具与安装状态已核验','engineering','engineering'),row('qualification','运动资格',!real||q.motion_qualified===true,real?(q.motion_qualified?'已报告控制器运动资格':'控制器运动资格未核验'):'仅仿真资格','按现场验收核对控制器、固件和工具资格','当前控制器所需运动能力已验证','engineering','tele')]
 const h=real?s.real_hand_follow:s.hand_follow
 for(const [i,b] of (h?.blockers||[]).entries())rows.push(row('follow-'+i,'交互启动条件',false,b.detail||'条件未通过','到交互页检查'+(b.layer||'启动条件'),'重新检查后该阻止项消失','hand-follow'))
 rows.push(row('vision','视觉',null,h?.blocked_reason||'按具体任务核验来源、帧龄与标定','到视觉页检查图像与标定','任务预检确认同源、新鲜图像和所需标定','vision'))
 const t=taskProjection(s,true),busy=t.owner!=='无活动任务'||t.stop==='pending'
 rows.push(row('owner','任务占用',!busy,busy?t.owner+' · '+t.phase:'当前无活动任务',busy?'等待结束，或停止并确认停稳':'选择任务','无其他活动控制权且停止已确认','work'))
 return rows
}
