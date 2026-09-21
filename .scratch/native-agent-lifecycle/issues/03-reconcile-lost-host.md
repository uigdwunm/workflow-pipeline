# 03 — 宿主丢失或回执中断后保留原身份与副作用

Status: ready-for-agent
Lifecycle: completed

**What to build:** 宿主或 owner 崩溃后，恢复命令对账原调用与已完成副作用，不重新派发或重复发布；无法证明原身份可恢复时给出明确保留现场的恢复状态。依据 Spec D3/D5/D6/D7、A2/A3/A6。

**Blocked by:** 01 — 原生角色等待期间保留同一前台宿主

- [x] 意图先持久化，原始 transport 回执绑定 record/instance/request/thread/turn；丢失响应与未知发送结果保留，不从 saved payload 重发 mutation。
- [x] EOF、协议错误、SIGTERM 与 checkpoint contention 撤销当前活性证明，同时保存已接受 B 结果、Git 文件/提交及全部未解决 action。
- [x] 无 owner resume 先摄入准确已完成 turn 和 C 原事务，再对账原 publication/cleanup；已经发出的业务调用与发布计数不增加。
- [x] 跨宿主原身份查找或 unresolved_action terminal outcome 缺证据时 await-host-recovery；不根据历史记录、PID、锁、标题或单一候选假定恢复。
- [x] 有明确未发出证据的 prelaunch 与已完成 run ACK 保持可用；旧 version 1/2/3、C v1–v5 只读，固定 package identity 不替换。
- [x] 生产 seam 故障注入覆盖保存意图前后、写入 pipe 后、回执保存后、主 checkpoint 保存失败；文件哈希和未解决动作保持，禁止 duplicate dispatch/publication。
- [x] 验证未知停止不会输出 paused/cancelled/completed；本地 process-group 清理和 native stopped 分开报告。

## Comments

由原生 to-tickets 行为生成；连续模式下粒度与阻塞关系已经按 Spec 中的切片决定确认。完成实现后才记录 Lifecycle: completed。

来源采用 Spec 的新冻结需求 commit `906efc37037c11d4a8b5ee99eac301079368fb8e`。实施遵循 Spec Further Notes 的 allowlist；运行时模板/协议及生成包属于实现候选，不能推迟到归档修补。

Closure: Intent/receipt reconciliation, transport-lost/uncertain outcomes, no duplicate dispatch/publication and read-only legacy rejection are implemented. Missing original-owner/native lookup evidence still requires host recovery; cross-host identity resurrection is not promised.

Accepted candidate `35d0c24d3729d6487751d80baabea1310a69a43a`; independent Standards and Spec accepted. Full suite: 671 tests OK, one existing skip; 28 host and 6 pipe/priority focused tests passed. Exact evidence and limits are recorded in `../PRD.md`, section "Stage 4 implementation acceptance and closure", and `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/candidate-delivery.json`. Checkmarks certify the agreed repository scope, not real nested integration; that external item is not passed and does not block local delivery.
