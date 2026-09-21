# 02 — 审查收敛后最终验证并形成可交付结果

Status: ready-for-agent
Blocked by: 01

**What to build:** 在两轴收敛后通过原调度者执行冻结的完整最终清单，只有准确同提交的成功证据能够经 B/C、runner 和 Stage-4 入口通过。

**Spec traceability:** Implementation Decisions §1–3、§5；需求验收 3、4。

- [ ] Controller 收敛决定绑定两轴语义结果与原生身份/停止证据；此前发起最终验证必须拒绝。
- [ ] 先持久化完整 attempt，再沿同一 dispatcher ref 发最终验证 followup；不新建角色或测试执行器。
- [ ] 必需项清单、命令/程序、结果来源、candidate/target/plan/review digest 全部核验；missing、failed、skipped、unknown、重复项或错误身份不能通过。
- [ ] B receive/accept 不从 completed 反向制造成功状态；runner 严格 handoff 和 Stage-4 start-closure 都复用相同交付规则。
- [ ] 最高 seam 测试记录调用顺序和真实 Git；旧非空 checks/list 在 direct control/B/runner 路径均拒绝。

## Testing seam

沿用 Spec 的公开 runner/C→真实 B/control/Git seam，只替换边界外宿主传输、耗时命令及故障；不 mock 本次变更边界。读取完整 Spec 后以本 Ticket 的行为为切片，不将模块单独当作交付。

## Authority and completion

同一 Implementation Dispatcher 和 Flow Worktree；无目标分支写入、无安装/部署/远程写入。Tickets 只记录切片和阻塞，不修改已确认方案。Stage 3 不编辑本 Ticket，Stage 4 按实际结果记录 Lifecycle。
