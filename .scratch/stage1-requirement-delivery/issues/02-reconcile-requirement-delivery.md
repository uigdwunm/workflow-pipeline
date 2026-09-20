# 02 — 中断和目标推进后复用同一交付

**What to build:** 同一阶段 1 交付在进程中断、hook 失败、提交响应丢失和目标无关推进后能从原 checkpoint 恢复；真实冲突和未知结果保留现场，不重新拷问、重复提交或另选需求。

Blocked by: 01
Status: ready-for-agent

实施依据：本特性 PRD 的 D2–D4、D6–D7；冻结需求验收 2–5、7。依赖 01 提供真实生产入口和 proof。测试仍从 C/CLI 入口进入，使用真实 Git 和精确故障注入；无需另建 recovery registry。

- [ ] C 在副作用前保存原 request/intent/issued，成功后保存完整 result；只占用现有 workflow_requirements 的 delivery 类型交易。
- [ ] 丢响应首先 reconcile；匹配 commit 必须核验唯一候选、父提交、全 owned bytes/mode、范围和可达性。
- [ ] 原前态/期望态组成的部分写入和本 intent 暂存可续做；冲突或非本 intent 状态停止且不覆盖。
- [ ] 提交已完成但工作文档/无关索引漂移时保留有限 completed_evidence；恢复原现场后重用同一提交。
- [ ] 目标无关前进且无既有副作用时，先对账再保存关联的新 intent，保留同一冻结来源与授权，无新增用户确认。
- [ ] 已交付后无关推进只读复用；owned 变更、非快进、对象不可达、多候选、actor/权限/目标变化明确停止。
- [ ] T5–T7 通过，且 T1–T4 重跑覆盖实现改变；重复请求不会创建第二份文档或第二次交付。
- [ ] hook/filter 行为、原 pin 和历史 checkpoint 保留，既有 freeze/write 交易回归通过；文档与生成包同步。

完成证据需列明每个中断点实际观测的目标 HEAD、交付对象数量、用户文件/索引保全及恢复结果。仅有 prepared/issued 或对象候选不能报告阶段完成。
