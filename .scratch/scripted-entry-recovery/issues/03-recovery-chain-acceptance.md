# 03 — 验收入口、原身份恢复和接管的完整链

Status: ready-for-agent
Lifecycle: completed
Blocked by: 01, 02

**What to build:** 用户获得可以区分继续、接管与无法恢复的脚本化结果；跨目录入口与中断后的执行能在同一原记录中对账，真实宿主能力边界可检查。

Spec: ../PRD.md — D1–D6、Testing Decisions；AC1–AC6。

- [x] 完整 runner/carrier/native 生产请求链贯通 01 的入口证据与 02 的恢复状态；允许只替换外部宿主，不替换核心决策/Git adapter。
- [x] running 仅等待；idle/resumable 仅同身份；unresolved call、business block、stop/cancel 及 publication intent 的优先级符合 D2。
- [x] 原宿主丢失输出 await-host-recovery、原身份、未决调用和具体缺项，无新宿主或重复角色创建；停止证明不会被报告为恢复证明。
- [x] 恢复再次中断和重复调用保持原意图/回执；现有发布对账及 cleanup-only 分支不产生二次提交或发布。
- [x] 基于 exact candidate/target/plan/source/attempt 验证结果复用，恢复导致候选改变时重新 review-converged 后才 full validation。
- [x] 同步涉及的文档与生成包并完成必要定向回归；最终完整检查按现有审查优先规则执行。
- [x] 交付报告分列确定性脚本回归与真实宿主验收；仅使用已有隔离设施和授权能力；缺真实 nested/host-loss 证据明确 pending/unsupported，不伪报通过，不操作旧业务任务。

Testing seam: T1 顶层完整链与独立真实宿主验收。03 以 01 的可信入口和 02 的可重入接管为前置，因此两个 blocking edges 均必要。

## Comments

原生 to-tickets 已发布。若真实宿主不支持旧身份恢复，交付严格阻塞与缺项报告即符合该分支；不得扩展为外部宿主改造。

完成证据：同一最终实现候选 `b72553d100330ef9e74abcbbeeb4769b3942d876` 已通过 Standards / Spec 双轴独立审查和最终完整验证（716 tests、1 existing skip、exit 0）；日志、摘要及验证边界见 [PRD Completion evidence](../PRD.md#completion-evidence)。本 Ticket 的确定性回归和文档范围已完成；真实旧宿主丢失/嵌套恢复仍为 pending/unsupported，未据此声明真实恢复能力。
