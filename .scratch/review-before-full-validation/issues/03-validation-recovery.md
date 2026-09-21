# 03 — 验证失败与暂停取消保持原证据和身份

Status: ready-for-agent
Blocked by: 02

**What to build:** 让验证失败、重试和进程中断继续原 Flow 与原身份，准确保留失败和未知结果，不越过停止优先或重放不确定操作。

**Spec traceability:** Implementation Decisions §3–4；需求验收 2、4、5。

- [ ] 失败但无代码变化可保留有效两轴审查，Controller 指向失败 attempt 决定后以新 attempt 完整重跑；旧失败不可覆盖。
- [ ] 较新失败遮蔽旧成功，不拼接各次检查通过项；代码变化重回定向检查和两轴审查，既有 diagnosis-first 仍有效。
- [ ] 暂停/取消早于业务动作；迟到结果只保存对账，不使 cancelled/paused 自动可交付。
- [ ] 覆盖调用前后、结果保存/消费前后故障；未知调用先对账且不重发，原宿主丢失保持 host recovery 阻塞。
- [ ] 保留历史审查者、执行分配及未绑定调用的原生停止屏障；重复结果 ACK，同 identity 不同内容拒绝。

## Testing seam

沿用 Spec 的公开 runner/C→真实 B/control/Git seam，只替换边界外宿主传输、耗时命令及故障；不 mock 本次变更边界。读取完整 Spec 后以本 Ticket 的行为为切片，不将模块单独当作交付。

## Authority and completion

同一 Implementation Dispatcher 和 Flow Worktree；无目标分支写入、无安装/部署/远程写入。Tickets 只记录切片和阻塞，不修改已确认方案。Stage 3 不编辑本 Ticket，Stage 4 按实际结果记录 Lifecycle。
