# 03 — 专用任务与讨论附着路径使用同一交付出口

**What to build:** Dedicated Problem Framing Task 和讨论附着阶段 1 在交付完成后才发送成功结果；原控制器继续认证接收、Phase 完成、后继激活与归档，普通/专用/附着路径使用一致的成功和待恢复语义。

Blocked by: 02
Status: ready-for-agent
Lifecycle: completed

实施依据：本特性 PRD 的 D1、D4–D7；冻结需求验收 1、4–7。依赖 02 的稳定交付恢复，避免专用任务丢响应时产生不一致结果。沿用真实 discussion ledger 与宿主适配测试替身；不扩展 carrier allowlist。

- [x] 专用任务的确认内容明确目标与交付责任；交付成功后才发送原 source ID 对应结果，payload 保留 source identity 并携带目标 proof。
- [x] receive/accept 与后继入口核验真实目标；重复认证消息 ACK，不重交付，不重建任务，不提前归档。
- [x] CP 完成只产生 checkpoint ref，普通分支/索引保持；后续有界交付单独推进目标，来源 CP、唯一 topic 文档身份保持。
- [x] pending DW、gate、错误 actor/attempt、Phase 权限继续由原协议决定；生产者不假冒 source 或绕过写权。
- [x] wrapper 与 same-stage 分别保持原 claim/accept/finalize/ready/activate/archive 顺序；结果未满足目标交付时没有 stage completed 证据。
- [x] 非 Git discussion 只保留 snapshot，不伪造 Git 交付；Stage 0 不获得新增权限。
- [x] stepwise 保留原 Stage 2 入口选择，continuous 的正常 1→2 不新增整合确认；T8–T11 通过。
- [x] 阶段入口、固定模板、共享契约与生成包消除 freeze-only 成功出口及无关 staged 修改阻塞的矛盾。
- [x] 运行确定性重建与完整仓库验证；报告真实 Git/CLI 覆盖和未执行的真实宿主现场验收。

阶段 4 仅在实现/双轴审查通过后更新本特性的 Lifecycle 与完成证据；不在本 Ticket 中部署本机或改写原冻结需求。

## Comments

### 阶段 4 完成证据（2026-09-20）

实现候选：`2233a49b78b18f8711ec25c6d87ea750b8e4b337`。Standards 与 Spec 两轴均已接受同一候选；审查引用分别为 `/root/sg_standard_standards_b5e726d_t_7aa95e6e9616`、`/root/sg_standard_spec_b5e726d_t_b7682d6c274b`。下列勾选表示已接受实现及自动化验证完成。

验证：29 项 requirement-delivery 针对性测试通过；完整 `scripts/validate.sh` 通过，655 项测试、1 项既有跳过，含确定性构建检查、包检查和 repository validator（运行记录 `/tmp/stage1-delivery-gate-full.log`）。恢复快照 SHA-256 与提交 blob 一致，候选工作区干净且 `git diff --check` 通过。阶段 4 仅修改本 PRD 与三个 Ticket 的 Lifecycle、复选框和完成证据，复用上述代码验证结果。

验证边界：真实 Git、文件、索引和 CLI 边界已自动化验证；宿主适配使用测试替身，真实宿主任务创建、消息认证/发送、归档和 UI 未现场验证。未进行本机部署或远程写入。冻结需求与方案语义保持不变。

生命周期证据：专用路径 delivery→receive→accept 与重复消息 ACK、讨论 Git CP 后有界交付、原权限 gate 及恢复边界均纳入针对性/既有回归测试；入口、模板及共享契约与生成包同步。这里的完成只覆盖已接受代码与自动化协议验证，不声称已执行真实宿主创建/认证/归档验收。
