# ADR-0011: 设计后封存阶段范围

Status: Accepted in part; active revision and legacy exception withdrawn — 2026-09-27 用户决定

Stage 2 启动时保留已知需求、规划写入权限和 Flow Worktree；未知下游范围保持 unresolved。设计者提出逐文件清单，原 Controller 封存，C 持久化，B 验证。planning_complete 与 handoff_ready 分离，Stage 3 必须验证当前封存权威与就绪证据。

范围校正仅允许在接受 Stage 2 后、任何 Stage-3 preparation、launch intent 或 session 保存前进行。已有这些记录即拒绝，即使取消或从未创建 writer；不回退阶段、不改变旧写入权威、不创建新的 writer attempt。正常业务、宿主、Dispatcher 恢复保留其原契约。

旧决定曾允许活动 Stage-3 范围修订和旧 Stage-2 一次性兼容接管。用户现已撤回两项能力，ADR-0008 原固定运行时规则不再有本 ADR 的例外。下方历史实施证据不构成恢复这些能力的待办或授权。

当前契约与验证边界见 `.scratch/stage-scope-finalization/PRD.md`。当前版本为 progress-v15、transfer-v10、control key 9/schema 5、runner outer 9；其余版本不变。本决策不执行现场迁移或安装，也不宣称本次检查已通过。

## 历史实施记录（撤回前）

实施记录（2026-09-27）：e24136f 实施基线已是 progress-v13、transfer-v8、control key 7/schema 5、runner outer 7；原决策当时实现为 progress-v14、transfer-v9、control key 8/schema 5、runner outer 8。旧 v12/v7 的原始记录与包 pin 仍是只读历史。源码已加入范围封存/修订、同 checkpoint 的窄接管以及附着 Topic 的阶段预留；本对话授权的是项目修复，没有对用户真实旧运行执行接管。完整包、仓库回归及失败夹具复测已完成，58 个测试模块合并覆盖 1,000 个用例（999 通过、1 个既有跳过），实际命令结果与剩余验证边界见 PRD 实施记录；真实附着旧 schema 4 正向 fixture 因缺原 WC 记录而按资格检查拒绝，其正向行为尚未验证。


收缩实施已完成：全量验证 943 项、零失败/错误、1 个既有跳过；五包构建一致性和仓库校验通过。当前支持正常范围封存与 Stage 3 前校正，撤回能力不再提供入口。
