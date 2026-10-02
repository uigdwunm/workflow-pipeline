# ADR-0013: Revise function plans within the accepted contract

Status: Accepted
Review: fep-solution-review-20261001-01
Authorization: user-current-conversation:confirm-continuous:fep-solution-review-20261001-01
Amends: ADR-0007 and ADR-0011 for new protocol runs only

Ticket 保留功能、验收和前置依赖合同，其内函数级执行计划由原 Workflow Controller 在既有 checkpoint 中修订；采用短暂全停止、精确规划写入窗口和一次 current Scope Authority 切换，使规划字节、文件权限、分配资格与恢复证据保持一致。保留原冻结需求、launch 和历史结果：未受影响结果按计划/依赖/验证/字节依据 carry forward，受影响字节经真实 Execution Agent 复核后重新验收。

新运行的 Implementation Dispatcher 只分配、验收、验证和 Git 集成，不编码或修改计划；bounded direct 例外只由旧运行原固定包保留。合同内私有函数移动、拆分、辅助函数和必要文件同步由原 Controller 自行判断；功能、业务职责、公共接口、验收或重大架构变化保留用户决定，内部修订不得修改这些合同或扩大业务授权。此选择明确收窄为新确认需求内的活动 Stage-3 计划修订，取代 ADR-0011 的对应禁止，但不恢复旧范围接管/迁移。

曾考虑把函数计划独立为一套新服务、让 Dispatcher 随编码同步修改、或要求每次局部调整重新审批；这些方案分别分裂权威、允许执行者自改验收依据、或重复打断用户。代价是共享 Worktree 的 HEAD/index 迫使每次正式内部修订暂停当前全部 writer；保留有效结果和精确恢复事务避免重新完成整个交付。普通函数体内等价编码选择不构成正式计划修订。

实施结构、事务分支与测试决策由 `.scratch/function-execution-plans/PRD.md` 拥有；此 ADR 不授予 Stage-2 实现、安装或迁移权限。
