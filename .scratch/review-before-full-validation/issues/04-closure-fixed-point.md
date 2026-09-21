# 04 — 目标变化回流与归档文档发布保持准确固定点

Status: ready-for-agent
Blocked by: 02

**What to build:** 让目标变化回原调度者重新形成可交付版本，同时允许受限归档文档提交在不冒充实现验证的前提下发布并恢复清理。

**Spec traceability:** Implementation Decisions §4–5、§7；需求验收 3、4、5。

- [ ] 审查、最终验证、B 接受、Stage-4 入口及发布前均核对 target；变化不允许只改 expected_target_head。
- [ ] 新 target 合入原 Flow 后生成 replacement commit，重新定向检查、两轴审查、最终验证；保护来源冲突走原异常。
- [ ] 实现提交 I 与归档提交 D 分离；D 只能改精确 closure_paths，源码 Skill Markdown、测试、配置及生成包不可归档修改。
- [ ] 严格证据在业务发布边界核验，保留 supervision 的底层 Git 职责和 standalone 文档闭包行为。
- [ ] 真实临时 Git 测试覆盖发布竞争、已 merge 后只清理/Phase Run、未知发布对账，不重复发布。

## Testing seam

沿用 Spec 的公开 runner/C→真实 B/control/Git seam，只替换边界外宿主传输、耗时命令及故障；不 mock 本次变更边界。读取完整 Spec 后以本 Ticket 的行为为切片，不将模块单独当作交付。

## Authority and completion

同一 Implementation Dispatcher 和 Flow Worktree；无目标分支写入、无安装/部署/远程写入。Tickets 只记录切片和阻塞，不修改已确认方案。Stage 3 不编辑本 Ticket，Stage 4 按实际结果记录 Lifecycle。
