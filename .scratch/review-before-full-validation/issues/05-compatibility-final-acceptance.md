# 05 — 新旧协议隔离、生成包一致性与最终验收

Status: ready-for-agent
Blocked by: 03, 04

**What to build:** 将前面行为串到新版本 runner/C/B/control 和所有自包含包，证明旧记录不被迁移，并在独立审查收敛后完成最终整体验证。

**Spec traceability:** Implementation Decisions §6–7、Testing Decisions 全部；需求验收 1–6。

- [ ] 新 runner v5、C v7、B v3、control schema 2 完整创建和传递；旧版本在写盘/host 调用前拒绝并保留原记录字节。
- [ ] attached control 外层 ledger 不变；旧嵌入 control 不静默改写，新旧包 compatibility key 不同则拒绝交接。
- [ ] 所有必要源契约与构建生成文件一致；既有版本 fixture 只更新相关预期，不删除旧版本拒绝测试。
- [ ] 真实 runner→C→B/control/Git 链覆盖正常交付、最终证据拒绝和恢复，standalone 不能走弱路径。
- [ ] 先完成定向检查和独立两轴审查；准确最终候选再执行完整 validate.sh 与必需真实单层 lifecycle/stop 验收，保留 raw reports 和实际 skip 依据。
- [ ] 真实嵌套仍明确外部待验证；不声称 transport double 证明现场能力，不安装或部署，不调查旧 EPERM。

## Testing seam

沿用 Spec 的公开 runner/C→真实 B/control/Git seam，只替换边界外宿主传输、耗时命令及故障；不 mock 本次变更边界。读取完整 Spec 后以本 Ticket 的行为为切片，不将模块单独当作交付。

## Authority and completion

同一 Implementation Dispatcher 和 Flow Worktree；无目标分支写入、无安装/部署/远程写入。Tickets 只记录切片和阻塞，不修改已确认方案。Stage 3 不编辑本 Ticket，Stage 4 按实际结果记录 Lifecycle。
