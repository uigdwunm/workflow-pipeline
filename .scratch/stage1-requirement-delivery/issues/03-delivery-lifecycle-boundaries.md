# 03 — 专用任务与讨论附着路径使用同一交付出口

**What to build:** Dedicated Problem Framing Task 和讨论附着阶段 1 在交付完成后才发送成功结果；原控制器继续认证接收、Phase 完成、后继激活与归档，普通/专用/附着路径使用一致的成功和待恢复语义。

Blocked by: 02
Status: ready-for-agent

实施依据：本特性 PRD 的 D1、D4–D7；冻结需求验收 1、4–7。依赖 02 的稳定交付恢复，避免专用任务丢响应时产生不一致结果。沿用真实 discussion ledger 与宿主适配测试替身；不扩展 carrier allowlist。

- [ ] 专用任务的确认内容明确目标与交付责任；交付成功后才发送原 source ID 对应结果，payload 保留 source identity 并携带目标 proof。
- [ ] receive/accept 与后继入口核验真实目标；重复认证消息 ACK，不重交付，不重建任务，不提前归档。
- [ ] CP 完成只产生 checkpoint ref，普通分支/索引保持；后续有界交付单独推进目标，来源 CP、唯一 topic 文档身份保持。
- [ ] pending DW、gate、错误 actor/attempt、Phase 权限继续由原协议决定；生产者不假冒 source 或绕过写权。
- [ ] wrapper 与 same-stage 分别保持原 claim/accept/finalize/ready/activate/archive 顺序；结果未满足目标交付时没有 stage completed 证据。
- [ ] 非 Git discussion 只保留 snapshot，不伪造 Git 交付；Stage 0 不获得新增权限。
- [ ] stepwise 保留原 Stage 2 入口选择，continuous 的正常 1→2 不新增整合确认；T8–T11 通过。
- [ ] 阶段入口、固定模板、共享契约与生成包消除 freeze-only 成功出口及无关 staged 修改阻塞的矛盾。
- [ ] 运行确定性重建与完整仓库验证；报告真实 Git/CLI 覆盖和未执行的真实宿主现场验收。

阶段 4 仅在实现/双轴审查通过后更新本特性的 Lifecycle 与完成证据；不在本 Ticket 中部署本机或改写原冻结需求。
