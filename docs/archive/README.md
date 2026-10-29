# 历史文档归档

归档清单：[MANIFEST](MANIFEST.json)。本轮日期2026-10-30。

历史阶段资料退出当前入口和默认检索，内容原字节保留；不是按年龄认定已修，也不是删除失败证据。
原README/HANDOFF先保存完整快照，再精简当前版本。旧生成文档连同.plan-documents.json一起保留，未强制覆盖生成器保护。

```powershell
node tools/docs.mjs locate docs/repair-report-2026-10-26.md
node tools/docs.mjs find "旧跟随" --history
node tools/docs.mjs check
```

历史内链按MANIFEST的original路径理解：先locate目标，再打开归档位置；不能假定搬迁后旧相对链接自动指向新路径。
offline_review.py是历史审查脚本，原本依赖旧目录并通过__file__计算根。归档保留仅为证据，禁止作为当前脚本直接执行。

恢复到新的目录或人工对比后逐项恢复，不覆盖当前文件。文件哈希证明原字节保留，不证明内容结论当前仍成立。
没有清理backend/data、模型资产、第三方许可或output检查产物。全局记忆库未在此复制或归档。
