# 01 — 可审查候选进入独立两轴审查

Status: ready-for-agent
Lifecycle: completed
Blocked by: None — can start immediately

**What to build:** 把完成定向与受影响检查的干净提交送入两个独立审查轴，同时机械拒绝将它当成已完成交付。

**Spec traceability:** Implementation Decisions §1–3、§7；需求验收 1、2、4。

- [x] 先通过真实 C→control/Git 生产边界建立红绿纵向测试：定向检查满足后可审查，未有最终证据的 completed 被拒绝。
- [x] 冻结验证清单及准确候选/目标/计划身份；candidate-ready 与最终不可替换 delivery 分开。
- [x] 两轴使用同一固定点和不同真实 reviewer ref，停止证明与语义 accepted 分开。
- [x] 失败定向检查、dirty 工作区、作用域/保护来源变化均拒绝送审；不执行完整验证作为审查前置。
- [x] 同步这条切片所需源码契约、生成副本及 fixture；后续未完成能力 fail closed，不提供旧宽松逃生分支。

## Testing seam

沿用 Spec 的公开 runner/C→真实 B/control/Git seam，只替换边界外宿主传输、耗时命令及故障；不 mock 本次变更边界。读取完整 Spec 后以本 Ticket 的行为为切片，不将模块单独当作交付。

## Authority and completion

同一 Implementation Dispatcher 和 Flow Worktree；无目标分支写入、无安装/部署/远程写入。Tickets 只记录切片和阻塞，不修改已确认方案。Stage 3 不编辑本 Ticket，Stage 4 按实际结果记录 Lifecycle。

## Comments

2026-09-21: Completed in accepted implementation 72d37ad011671a2fcd52c69279fd8e606e55c731 after independent Standards and Spec review. The PRD closure record preserves all review remediation, the failed final attempt, the 109-to-110 test-only scope addendum, and the fresh final attempt final-ec656333-6b4c-4c76-991f-b1f636865283: 696 tests OK with exactly one preapproved NonGit skip, both fresh real single-layer cases passed. Checkmarks include deterministic production-chain verification, not proof of real nested host capability. Real nested integration remains pending-external-dependency. No installation, deployment, remote write, or source/permission/native identity resurrection is claimed.
