<script setup>
import { PROGRAM_COMMANDS, TOOL_ACTIONS } from '../programLanguage'
const syntax = [
 ['如果 / 否则如果 / 否则','if / elif / else','条件分支；条件后加冒号，正文缩进 4 空格'],
 ['循环 i 在 范围(3):','for i in range(3):','遍历 0、1、2；可遍历列表、字典和嵌套循环'],
 ['当 条件:','while condition:','条件成立时循环；每轮重新计算条件'],
 ['定义 作业(点):','def job(point):','具名函数，固定参数；支持返回值和嵌套调用'],
 ['返回 / 跳出 / 继续 / 略过','return / break / continue / pass','函数返回、退出当前循环、继续下一轮、空语句'],
 ['真 / 假 / 空','True / False / None','布尔值和空值'],
 ['且 / 或 / 非 / 在','and / or / not / in','布尔运算和成员判断；英文区分大小写'],
 ['配置 = {"速度": 15}','config = {"speed": 15}','变量用 = 赋值；+=、-= 等更新；== 比较'],
 ['路径 = [P1, "取料点"]','path = [P1, "pick"]','列表支持索引、切片、修改、解包；索引从 0 开始'],
 ['设置 = {"次数": 2}','settings = {"count": 2}','字典通过键读取、赋值；字符串使用半角引号'],
]
const functions = [
 ['范围','range','范围(起点, 终点, 步长)；也可仅写终点，终点不包含'],
 ['长度','len','返回列表、字典、字符串长度'], ['枚举','enumerate','返回下标与值；循环 i, 点 在 枚举(路径):'],
 ['配对','zip','配对多个列表，长度取最短列表'], ['整数 / 小数 / 字符串','int / float / str','类型转换'],
 ['列表 / 字典','list / dict','创建容器或复制已有容器'], ['绝对值 / 最小值 / 最大值','abs / min / max','数值运算'],
 ['求和 / 取整','sum / round','列表求和；取整(值, 小数位数)'], ['读取输入','DI','读取输入("ready")；读取当前 IO 映射，可用于条件判断'],
 ['列表.添加 / 弹出','list.append / pop','添加(值)；弹出() 或 弹出(下标)'],
 ['字典.获取 / 更新','dict.get / update','获取(键, 默认值)；更新({键: 值})'],
 ['字典.键 / 值 / 条目 / 弹出','dict.keys / values / items / pop','返回键列表、值列表、键值对列表，或移除并返回值'],
]
const details = {
 SPEED:'速度(15)：1–100%，与当前驱动的速度设置一致。', MOVEJ:'关节移动(P1)、关节移动("取料点") 或 关节移动([10,20,30,40,50,60])；按关节空间运动。',
 MOVEL:'直线移动(P1)、直线移动("取料点") 或 直线移动([260,-230,80,0,90,0])；末端直线运动，六轴通过逆解联动，目标须可达。',
 DOUT:'输出(0,1) 或 输出("vacuum",1)：末端位号 0–2 或已配置 IO 名称，值 0/1。',
 WAIT:'等待(0.5)：等待秒数，范围 0–60。', WAIT_DI:'等待输入("ready",1,timeout=5,debounce=0.1)：等待输入稳定到指定值；超时中止程序。',
 PAUSE:'暂停()：等待人工点击继续；停止会中止程序。', TOOL:'工具("打开") 或 TOOL("OPEN")：动作按已确认工具和 IO 映射执行。',
 REQUIRE_TOOL:'需要工具("工具档案ID")：当前确认的工具必须匹配。',
 IF_DI:'旧语法：如果输入 ready 1 GOTO done；输入符合时跳到标签。新程序推荐 如果 读取输入("ready") == 1:。',
 LABEL:'旧语法：标签 done；与 GOTO 配合。', LOOP:'旧语法：循环 2 … 结束；兼容旧文件，不能与 Python 缩进块混用。', END:'旧语法：结束，关闭旧式循环。',
}
const chinese = `配置 = {"速度": 15, "次数": 2}
路径 = [P1, "取料点"]

定义 搬运(目标):
    关节移动(目标)
    等待(0.2)
    返回 真

速度(配置["速度"])
循环 次数 在 范围(配置["次数"]):
    循环 点 在 路径:
        如果 读取输入("ready") == 1:
            搬运(点)
        否则:
            暂停()`
const english = `config = {"speed": 15, "count": 2}
path = [P1, "pick"]

def transfer(target):
    MOVEJ(target)
    WAIT(0.2)
    return True

SPEED(config["speed"])
for cycle in range(config["count"]):
    for point in path:
        if DI("ready") == 1:
            transfer(point)
        else:
            PAUSE()`
</script>
<template>
<section class="panel"><div class="panel-hd"><span class="panel-title">中英文编程规则</span></div><div class="panel-bd programming-rules">
<p>默认中文补全。头部中文 / EN 切换改变提示和插入语言，已有代码保留。机器人指令可混写中文和英文；Python 关键字和普通变量区分大小写。</p>
<p>推荐函数调用与 Python 风格缩进：半角括号、逗号、冒号，每层 4 空格。# 注释可独占一行或写在行尾。代码支持变量、嵌套控制和函数，并非完整 Python 运行环境。</p>
<details open><summary>机器人指令</summary><table><thead><tr><th>中文 / 英文</th><th>参数与行为</th></tr></thead><tbody><tr v-for="c in PROGRAM_COMMANDS" :key="c.en"><td>{{ c.zh }}<br /><code>{{ c.en }}</code></td><td>{{ details[c.en] }}</td></tr></tbody></table></details>
<details open><summary>点位引用、数据与残影</summary><p>P1、P2 是保存点的固定编号；改名不改变编号。可写 关节移动(P1)、直线移动(P1) 或 关节移动("取料点")。含空格的名称需加引号。列表可保存点位引用供循环使用。</p><p>输入运动函数参数时显示点位候选；选择后显示编号、名称、J1–J6 和 X/Y/Z/A/B/C 保存值。编号旁的「影」显示关节目标残影，不执行运动；直线预览只显示终点，不代表整条轨迹已通过检查。</p><p>关节角和 A/B/C 单位 °，X/Y/Z 单位 mm。数据标明保存来源；TCP 默认由保存的六轴角正解计算。程序解析时固定点位快照，修改点位后应重新解析。</p></details>
<details open><summary>变量、容器与控制流</summary><table><thead><tr><th>中文 / English</th><th>规则</th></tr></thead><tbody><tr v-for="row in syntax" :key="row[1]"><td><code>{{ row[0] }}</code><br /><code>{{ row[1] }}</code></td><td>{{ row[2] }}</td></tr></tbody></table><p>运算支持 +、-、*、/、//、% 和 ==、!=、&lt;、&lt;=、&gt;、&gt;=。函数参数使用局部作用域，不支持 global/nonlocal。循环可嵌套，支持 for/while 的 else 块。</p></details>
<details><summary>内置函数和容器方法</summary><table><thead><tr><th>中文 / English</th><th>用途</th></tr></thead><tbody><tr v-for="row in functions" :key="row[1]"><td>{{ row[0] }}<br /><code>{{ row[1] }}</code></td><td>{{ row[2] }}</td></tr></tbody></table></details>
<details><summary>工具动作</summary><table><tbody><tr v-for="action in TOOL_ACTIONS" :key="action.en"><td>{{ action.zh }}</td><td><code>{{ action.en }}</code></td></tr></tbody></table></details>
<details><summary>中文完整示例</summary><pre>{{ chinese }}</pre><p>示例需先保存 P1，并将另一点命名「取料点」，配置 ready 输入映射。</p></details>
<details><summary>English example</summary><pre>{{ english }}</pre><p>示例需先保存 P1 和名称为 pick 的点位，配置 ready 输入。</p></details>
<details><summary>参数、旧文件与运行约束</summary><p>任务参数栏写 speed=12; point=P1。函数式程序直接使用变量 speed、point；旧程序用 ${speed}、${point}。保留原先每行 SPEED 15、MOVEJ P1、WAIT 0.5 的格式，旧式循环不支持嵌套。一个文件选用一种格式。</p><p>普通机器人动作串行执行，运动完成才继续。等待和暂停保留程序上下文。中止后重新启动从头运行；服务重启后不支持恢复脚本局部变量。动态循环显示已执行步数，不预估总进度。</p><p>最多 10000 语法节点、单循环 10000 次、100000 次计算、16 层函数调用；列表、字典和字符串长度最多 10000。禁止 import、文件/网络访问、eval/exec、任意对象属性、类、异步、推导式、装饰器和可变参数。运行错误显示行号并中止后续动作。</p><p>本项目语言不等同于厂商示教器文件格式；真机运动继续经过现有许可、工具和执行流程。</p></details>
</div></section>
</template>
<style scoped>.programming-rules { font-size: 12px; line-height: 1.7; }.programming-rules table { width: 100%; table-layout: fixed; }.programming-rules td { white-space: normal; overflow-wrap: anywhere; vertical-align: top; }.programming-rules th:first-child { width: 42%; }details { padding: 6px 0; }summary { cursor: pointer; color: var(--acc); font-weight: 600; }pre { overflow: auto; padding: 10px; background: var(--bg-inset); font: 11px/1.7 var(--font-mono); }code { color: var(--acc); font-size: 11px; }</style>
