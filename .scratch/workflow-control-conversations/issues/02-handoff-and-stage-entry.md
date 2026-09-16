# 02 — 验证接管后归档并支持明确阶段路线

**What to build:** 总控幂等接收专用结果，确认并验证下一阶段接管后归档旧任务；0可明确连续进入2–4，简单改动可明确走无讨论绑定的独立3。

**Blocked by:** 01 — 总控准备并交付专用0/1任务。

Status: ready-for-agent
Lifecycle: completed

Spec依据：I4及I8；需求D4–D6/D10；测试T4–T5。依赖01的控制身份、载体与gate。测试真实Control CLI及讨论phase运行边界、Git文档hash；外部任务动作使用有trace的替身。

- [x] 身份/版本/提交/hash不符拒绝；相同delivery仅ack，不重复接受或启动。
- [x] 顺序固定accept→ready/适用的activate→archive；每个失败位置保留旧任务和恢复事实。
- [x] 归档不确定先对账；已归档只补记；archive-pending不重复执行已接管阶段。
- [x] 0/1连续授权绑定最新CP、范围和总控；0不伪造Stage-1 result，拒绝drift/impact/gate和跨话题授权。
- [x] 合格0→3独立brief保留总控但无binding/Phase Run；不合格零工作树/任务写入，坏2交接不自动降级。
- [x] 交付替换先archive规则与组合确认模板的精确待归档文档。
