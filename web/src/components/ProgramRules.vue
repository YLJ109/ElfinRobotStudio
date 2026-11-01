<script setup>
import { PROGRAM_COMMANDS } from '../programLanguage'
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
const details = {
 SPEED:'速度(15)：1–100%，与当前驱动的速度设置一致。', MOVEJ:'关节移动(P1)、关节移动("取料点") 或 关节移动([10,20,30,40,50,60])；按关节空间运动。',
 MOVEL:'直线移动(P1)、直线移动("取料点") 或 直线移动([260,-230,80,0,90,0])；末端直线运动，目标须可达。',
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
<section class="panel"><div class="panel-hd"><span class="panel-title">ElfinScript 语法</span></div><div class="panel-bd programming-rules">
<p>本项目的受限机器人脚本，支持 Python 风格与旧式逐行指令；不是完整 Python 或厂商示教器格式。中文 / EN 切换会转换可识别的指令和关键字，字符串和注释不变，英文关键字区分大小写。</p>
<p>使用半角括号、逗号、冒号，每层缩进 4 空格；# 开头为注释。点位写 P1 或 "取料点"，含空格时加引号。</p>
<details open><summary>机器人指令</summary><table><thead><tr><th>中文 / 英文</th><th>参数与行为</th></tr></thead><tbody><tr v-for="c in PROGRAM_COMMANDS" :key="c.en"><td>{{ c.zh }}<br /><code>{{ c.en }}</code></td><td>{{ details[c.en] }}</td></tr></tbody></table></details>
<details open><summary>变量、容器与控制流</summary><table><thead><tr><th>中文 / English</th><th>规则</th></tr></thead><tbody><tr v-for="row in syntax" :key="row[1]"><td><code>{{ row[0] }}</code><br /><code>{{ row[1] }}</code></td><td>{{ row[2] }}</td></tr></tbody></table><p>支持 +、-、*、/、//、% 和 ==、!=、&lt;、&lt;=、&gt;、&gt;=；循环可嵌套。</p></details>
<details open><summary>中文示例</summary><pre>{{ chinese }}</pre><p>需先保存 P1 和「取料点」，并配置 ready 输入映射。</p></details>
<details><summary>English example</summary><pre>{{ english }}</pre><p>示例需先保存 P1 和名称为 pick 的点位，配置 ready 输入。</p></details>
<p class="tiny dim">动作按行串行执行；中止后从头运行。最多 10000 语法节点、单循环 10000 次；禁止 import、文件/网络访问和 eval/exec。</p>
</div></section>
</template>
<style scoped>.programming-rules { font-size: 12px; line-height: 1.7; }.programming-rules table { width: 100%; table-layout: fixed; }.programming-rules td { white-space: normal; overflow-wrap: anywhere; vertical-align: top; }.programming-rules th:first-child { width: 42%; }details { padding: 6px 0; }summary { cursor: pointer; color: var(--acc); font-weight: 600; }pre { overflow: auto; padding: 10px; background: var(--bg-inset); font: 11px/1.7 var(--font-mono); }code { color: var(--acc); font-size: 11px; }</style>
