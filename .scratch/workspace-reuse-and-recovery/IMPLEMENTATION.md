# 实施与验收记录

Scope: 已批准的 PRD D1–D4；两项受保护交互保持不变
State: 实现及回归验收完成；未安装到本机

## 实际改动

- 新增只读 `bind-worktree`，核验已有空间的 Git 身份、干净状态、任务提交历史及最终差异；接纳不授予删除权限。
- Worktree Binding 增加来源、管理方式、处置和创建证明。复用／宿主管理空间保留；脚本临时空间按原身份和非强制规则清理。绑定、C/B 验收、runner 和中断恢复使用同一处置结果。
- C 持久保存 `workspace_evidence`。保留路径保存原始选择／占用检查工具回执，脚本路径保存实际创建回执；阶段继承及重试不得替换。
- Stage 2 当前对话 Controller 可在独立来源 checkout 冻结需求，并通过原有有界文档事务交付到明确目标。来源与目标提交分别保留，整合目标后接纳同一空间。
- 方案设计者复用受控恢复路径。原宿主证明停写、结清调用、固定成果后，原 Controller 可在既有授权内替换执行者；替换先只读，核验后激活。
- 讨论附着的 Stage 2 保留原 Phase Run，通过完整已激活恢复链核验新设计者；未修改专用拷问对话或子话题首轮门槛。
- runner 先冻结宿主实际选定的路径，再验证绑定；删除旧的无条件脚本创建指令。

## 兼容边界

本候选使用 control compatibility 7 / schema 5、runner 7、entry v4、preparation v5、supervision v3、stage-transfer v8、workflow-progress v13、requirement-delivery v2。五个生成包已同步，不混用新旧协议。旧运行仍需要其原固定包；未实施迁移或本机部署。

## 独立审查

行为／需求审查发现讨论附着的恢复身份未贯通；修复后复审通过，并增加完整 ledger → C/B → Git 发布 → 原 Phase 完成测试。

协议／规则审查发现 runner 的旧脚本创建指令和工作区证据缺少持久保存入口；已修复并复审通过。审查使用源码，不以生成包重复计数。

## 验证记录

已通过新增工作区接纳与保留、逐提交越界检查、独立来源冻结及有界交付、方案执行者恢复专项测试。新增真实 Git 测试验证同一来源空间可继续进入方案且主目录的暂存／未暂存修改保持原样。

完整 `scripts/validate.sh --jobs 2` 执行了 45 个测试文件、891 项测试，1 项按既有条件跳过。首次完整运行报告 4 个 failure、4 个 error：旧删除专用结果、手工构造的旧绑定／检查点缺少生命周期证据，以及新增 runner 提示词测试未提供规范化字段。均为测试夹具字段不完整，已修正；这 8 项分别重测通过（3 + 2 + 1 + 2 项）。原全量命令的首次退出码仍为 1，不将其改写为一次性通过；最终结论依据完整覆盖与失败项复验合并取得。

失败项复验覆盖：整体完成证明、实现问题返回、direct 实现最终交付、连续三阶段链路、逐阶段三阶段链路、旧阶段归档恢复、linked source 身份、runner 预选空间提示词。另补充独立空间需求冻结 → 有界交付 → 同空间绑定 → Stage-2 输入准备的完整测试，已通过。

全量运行包含新增的方案 Agent 恢复（8 项）、附着讨论恢复（1 项）、独立需求空间（4 项）、保留空间全阶段交接（3 项），以及扩展后的工作区协议测试（40 项）。源码与五包生成一致性、Skill 格式校验、仓库规范检查、`git diff --check` 均通过。最终累计 890 项通过、1 项既有跳过，无未解决失败。

以下源码与基线逐字节一致：`src/stages/problem-framing/SKILL.md.in`、`src/stages/problem-framing/references/dedicated-grilling-protocol.md`、`src/shared/references/design-discussion/child-topic-protocol.md`、`src/shared/references/design-discussion/split-gate-contract.md`。

现场边界：测试使用真实临时 Git 仓库及宿主边界替身；不把替身测试当作真实嵌套 Agent／桌面恢复验收。本次实际通过宿主工具创建开发工作区，但完整新协议仍未部署到本机。
