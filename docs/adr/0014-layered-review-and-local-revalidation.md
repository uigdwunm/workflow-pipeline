# ADR-0014: Layer review evidence and localize revalidation

Status: Accepted
Review: accepted — 当前对话用户接受方案并选择连续模式
Amends: ADR-0013 的结果/审查失效策略（仅新协议运行）；保留共享Git全writer停止屏障

## Context

当前全部实施后的完整候选两轴审查和替换候选重审，使局部修复重复承担全交付成本。Implementation Ticket及Function Execution Plan已有合同、依赖和指纹，但独立审查没有可携带适用性证据的层次。共享Worktree的HEAD/index又不支持只暂停单个文件writer后安全提交规划。

## Decision

每个行为片独立上下文实施，片内修复优先复用原Execution Agent。局部一个独立reviewer覆盖规范与局部合同；同一reviewer可验收Ticket组合行为；交付两个轴只查跨Ticket、共享状态和完整需求覆盖。reviewer上下文受预算限制，不强制每次新建，也不得是被审查字节的实现者。

Evidence Claim绑定原计划、合同、读写指纹、依赖、测试环境与原生停止回执。跨候选保留需有独立适用性记录，沿明确依赖计算失效闭包；文件内无法证明互不影响则按文件失效，公共/未知共享影响扩大范围。最终候选必须有完整coverage certificate和针对该候选的完整验证，提前Ticket/受影响集成不能被最终验证替代。

正式Internal Plan Revision仍短暂停止全部共享writer，仅规划写入、提交、authority切换占屏障；未受影响工作保留结果和上下文，新lease引用新authority。普通函数体内等价选择不触发正式修订；合同/业务变化仍交用户决定。

## Alternatives and consequences

保留“所有候选全范围重审”最简单但重复成本高；每层均新建两个reviewer机械加层会更慢；函数级语义等价自动证明太复杂且不可靠。本决策付出claim依赖和适用性验证成本，换取明确局部性。文件级保守失效可能扩大审查，这是可解释的安全折中。全writer短停未消失，不能承诺修订完全并行。

详细契约、失败分支、角色数量及测试见 [.scratch/review-state-efficiency/PRD.md](../../.scratch/review-state-efficiency/PRD.md)。本ADR已接受，适用于本次新协议实现；不迁移旧运行。
