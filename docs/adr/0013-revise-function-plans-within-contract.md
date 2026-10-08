# ADR-0013: Revise function plans within the accepted contract

Status: Accepted
Review: fep-solution-review-20261001-01
Authorization: user-current-conversation:confirm-continuous:fep-solution-review-20261001-01
Amends: ADR-0007 and ADR-0011 for new protocol runs only
Amended by: ADR-0014 for layered review and result applicability in new protocol runs; the shared Git stopped-writer barrier remains.

Ticket 保留功能、验收和前置依赖合同，其内函数级执行计划由原 Workflow Controller 在既有 checkpoint 中修订；采用短暂全停止、精确规划写入窗口和一次 current Scope Authority 切换，使规划字节、文件权限、分配资格与恢复证据保持一致。保留原冻结需求、launch 和历史结果：未受影响结果按计划/依赖/验证/字节依据 carry forward，受影响字节经真实 Execution Agent 复核后重新验收。

新运行的 Implementation Dispatcher 只分配、验收、验证和 Git 集成，不编码或修改计划；bounded direct 例外只由旧运行原固定包保留。合同内私有函数移动、拆分、辅助函数和必要文件同步由原 Controller 自行判断；功能、业务职责、公共接口、验收或重大架构变化保留用户决定，内部修订不得修改这些合同或扩大业务授权。此选择明确收窄为新确认需求内的活动 Stage-3 计划修订，取代 ADR-0011 的对应禁止，但不恢复旧范围接管/迁移。

曾考虑把函数计划独立为一套新服务、让 Dispatcher 随编码同步修改、或要求每次局部调整重新审批；这些方案分别分裂权威、允许执行者自改验收依据、或重复打断用户。代价是共享 Worktree 的 HEAD/index 迫使每次正式内部修订暂停当前全部 writer；保留有效结果和精确恢复事务避免重新完成整个交付。普通函数体内等价编码选择不构成正式计划修订。

实施结构、事务分支与测试决策由 `.scratch/function-execution-plans/PRD.md` 拥有；此 ADR 不授予 Stage-2 实现、安装或迁移权限。

## 本地交付记录 — 2026-10-05

用户以“先交付原需求吧”确认本次交付 Ticket → 函数执行计划 → Dispatcher/Execution Agent → 原 Controller 内部修订主流程，延期补充入口。生产候选 `6d5bbb2` 已通过完整仓库验证；相同生产包字节的真实 Ticket 宿主样例完成私有修订、保留与复核、旧计划拒绝、公共/未知影响用户门禁、暂停/原调用恢复及当前计划的 Stage-4 接收、合并和清理。详情见 PRD 交付记录。

无 A / 无 Ticket 的独立入口尚缺合法的 C 修订初始化桥；foreground 的 Controller ingress 不可用；attached ledger 的真实 Stage-2 / Phase-0 初始化冲突导致启动拒绝。三条补充路线未验收、不纳本次交付，原方案承诺与失败证据保留。选择先交付核心行为的代价是尚不能承诺这些入口具有同样的内部修订能力。

当前生产包实际为 `1.3.0`、progress-v19、transfer-v14、runner 10、control key 11/schema 6；冻结 Spec 的原版本声明作为规划历史保留。本次为用户最新授权的普通本地交付，旧 production C v17 仍保留于 pausing 检查点，不改写为正式 Stage-4 成功；不迁移旧记录，不安装、部署或远程发布。
