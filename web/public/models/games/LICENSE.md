# 棋类 3D 模型来源与许可

## 国际象棋棋子

- 来源：Khronos Group glTF Sample Assets —「A Beautiful Game」
- 仓库路径：<https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/ABeautifulGame>
- 许可：Creative Commons Attribution 4.0 International（CC-BY-4.0）
- 本项目的改动：只保留每种棋子一个白/黑网格，删除棋盘、重复棋子与全部贴图，网格做简化；颜色由程序运行时按阵营设置。
- 生成脚本：`web/scripts/build-game-assets.mjs`，输出 `web/public/models/games/chess_pieces.glb`。

## 围棋 / 五子棋 / 中国象棋 / 井字棋

这些棋子是本项目按真实棋具外形生成的三维几何（凸透镜形围棋子、带刻字的圆形象棋子、环形与交叉井字棋），没有引入第三方模型，不涉及额外许可。

## 说明

3D 棋类显示层只按 2D 规则状态渲染棋子、棋盘和高亮，不驱动真实机器人，也不构成实体取放资格。
