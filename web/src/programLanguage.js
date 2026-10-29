// 一个目录同时供编辑器补全、高亮和「关于」页规则使用。
export const PROGRAM_COMMANDS = [
  { en: 'SPEED', zh: '速度', aliases: ['s', 'sd', 'sudu'], args: '1..100' },
  { en: 'MOVEJ', zh: '关节移动', aliases: ['j', 'g', 'gj', 'gjyd', 'guanjie'], args: 'P1 / J1,J2,J3,J4,J5,J6' },
  { en: 'MOVEL', zh: '直线移动', aliases: ['z', 'zx', 'zxyd', 'zhixian'], args: 'P1 / X,Y,Z,A,B,C' },
  { en: 'DOUT', zh: '输出', aliases: ['sc', 'shuchu'], args: '0..2,0|1' },
  { en: 'WAIT', zh: '等待', aliases: ['d', 'dd', 'dengdai'], args: '0..60' },
  { en: 'WAIT_DI', zh: '等待输入', aliases: ['ddsr'], args: 'name 0|1 TIMEOUT 3' },
  { en: 'IF_DI', zh: '如果输入', aliases: ['r', 'rgsr'], args: 'name 0|1 GOTO label' },
  { en: 'LOOP', zh: '循环', aliases: ['x', 'xh', 'xunhuan'], args: '1..200' },
  { en: 'END', zh: '结束', aliases: ['js', 'jieshu'], args: '' },
  { en: 'PAUSE', zh: '暂停', aliases: ['zt', 'zanting'], args: '' },
  { en: 'LABEL', zh: '标签', aliases: ['b', 'bq', 'biaoqian'], args: 'name' },
  { en: 'REQUIRE_TOOL', zh: '需要工具', aliases: ['xygj'], args: 'tool_id' },
  { en: 'TOOL', zh: '工具', aliases: ['gj', 'gongju'], args: 'OPEN / CLOSE / VACUUM_ON / VACUUM_OFF' },
]

export const TOOL_ACTIONS = [
  { en: 'OPEN', zh: '打开', aliases: ['d', 'dk'] },
  { en: 'CLOSE', zh: '关闭', aliases: ['g', 'gb'] },
  { en: 'VACUUM_ON', zh: '吸附', aliases: ['x', 'xf'] },
  { en: 'VACUUM_OFF', zh: '释放', aliases: ['s', 'sf'] },
  { en: 'ATTACH', zh: '连接', aliases: ['l', 'lj'] },
  { en: 'DETACH', zh: '脱离', aliases: ['t', 'tl'] },
]

export const PYTHON_WORDS = [
  { en: 'for', zh: '循环', aliases: ['xh'], args: 'i 在 范围(3):', enArgs: 'i in range(3):', snippet: true },
  { en: 'while', zh: '当', aliases: ['dq'], args: '条件:', enArgs: 'condition:', snippet: true },
  { en: 'if', zh: '如果', aliases: ['rg'], args: '条件:', enArgs: 'condition:', snippet: true },
  { en: 'elif', zh: '否则如果', aliases: ['fzrg'], args: '条件:', enArgs: 'condition:', snippet: true },
  { en: 'else', zh: '否则', aliases: ['fz'], args: ':', enArgs: ':', snippet: true },
  { en: 'def', zh: '定义', aliases: ['dy'], args: '函数(参数):', enArgs: 'function(arg):', snippet: true },
  { en: 'return', zh: '返回', aliases: ['fh'], args: '值', enArgs: 'value' },
  { en: 'break', zh: '跳出', aliases: ['tc'], args: '' },
  { en: 'continue', zh: '继续', aliases: ['jx'], args: '' },
  { en: 'range', zh: '范围', aliases: ['fw'], args: '起点,终点,步长', enArgs: 'start,stop,step', callable: true },
  { en: 'len', zh: '长度', aliases: ['cd'], args: '容器', enArgs: 'collection', callable: true },
  { en: 'DI', zh: '读取输入', aliases: ['dqsr'], args: '"输入名称"', enArgs: '"input_name"', callable: true },
  ...[
    ['abs','绝对值','jdz'], ['min','最小值','zxz'], ['max','最大值','zdz'], ['sum','求和','qh'], ['round','取整','qz'],
    ['int','整数','zs'], ['float','小数','xs'], ['str','字符串','zfc'], ['list','列表','lb'], ['dict','字典','zd'],
    ['enumerate','枚举','mj'], ['zip','配对','pd'],
  ].map(([en,zh,alias]) => ({ en,zh,aliases:[alias],args:'值',enArgs:'value',callable:true })),
  { en: 'True', zh: '真', aliases: [], args: '' }, { en: 'False', zh: '假', aliases: [], args: '' },
  { en: 'None', zh: '空', aliases: [], args: '' },
  { en: 'and', zh: '且', aliases: [], args: '' }, { en: 'or', zh: '或', aliases: [], args: '' },
  { en: 'not', zh: '非', aliases: [], args: '' }, { en: 'in', zh: '在', aliases: [], args: '' },
]
export const CONTAINER_METHODS = [
  ['append','添加','tj'], ['pop','弹出','tc'], ['get','获取','hq'], ['update','更新','gx'],
  ['keys','键','j'], ['values','值','z'], ['items','条目','tm'],
].map(([en,zh,alias]) => ({en,zh,aliases:[alias],args:'参数',enArgs:'arguments',callable:true}))

export function matchingCommands(items, prefix) {
  const key = prefix.toLowerCase()
  return items.filter(item => [item.en, item.zh, ...item.aliases].some(value => value.toLowerCase().startsWith(key)))
}
