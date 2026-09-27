# 03 — 一次性接管旧 Stage-2 交接

Status: ready-for-agent
Review: tickets-review-20260926-01 — accepted
Blocked by: 01
Spec: ../PRD.md — D5、D6；ADR-0011；Testing Decisions
Requirement: ../../../docs/requirements/2026-09-26-stage-scope-finalization.md

**What to build:** 对旧 v12/v7 Stage-2 已验收且 Stage 3 从未启动的流程，原 Controller 可保留已发布规划与同一 Flow Worktree，通过补充范围授权建立一次性新交接。完整保存旧原始记录和 pins，显式接管唯一 current，并让旧运行入口拒绝续写；不重做设计或出版，不迁移已进入实现的旧运行。

- [ ] 只接受 Spec 明确的旧版本组合，preparation v3/v4 分别验证；其他版本保留 original-runtime 拒绝，不猜字段或替换旧包。
- [ ] inspect 全程只读，核对旧包摘要、原宿主身份/停止、旧 accepted、真实规划出版、冻结需求 bytes/mode、binding 和 Topic/runner 状态。
- [ ] 全部历史中任何 Stage-3 prepared input、dispatch intent、session/call 或 allocation 均拒绝，包括后来未创建或取消的情况；未决出版/接受/Phase 事务先回原流程处理。
- [ ] 补充授权绑定完整旧快照身份、新 manifest、新 pins、同一需求/规划/工作区及原 Controller；仅凭摘要或布尔字段不能授予恢复权。
- [ ] 原 Controller 的旧包写命令、carrier、runner 和相关作者全部停写且调用结清后，按 RunLock→record lock→ledger 顺序重验；拿锁或超时不代替停止证明。
- [ ] 单次原子文件替换保存完整旧 checkpoint 原始 UTF-8 字节/hash，并把唯一 current 切到 v13 takeover-pending；runner 当前 outer 为新 7，旧 outer/confirmed/sessions/pins 原貌留在快照。
- [ ] 旧 C protocol guard、旧 runner version guard 和旧附着 schema guard 阻止接管后的续写；旧只读 verifier 接收冻结旧对象，不运行旧 runner，也不把新 current 当旧记录。
- [ ] 附着接管先本地 pending，再原 ledger 幂等 CAS，再精确回读激活；任何中断窗口无派发权限，不回退旧 current，不新增活动 ledger。
- [ ] canonical checkpoint + 原 accepted digest 只有一个有效补充授权；复制 JSON、换路径/Controller、复用 ID 改内容均不能重复消费。
- [ ] 消费与 Stage-3 launch intent 在当前 checkpoint 同次锁内保存；附着 reservation 经原事务确认后消费；丢响应仅 reconcile 原 intent。
- [ ] 消费后回到普通 v13 current 执行路径，不长期使用 recovery_id 选择另一活动投影；新 host admission 不替代旧 host 停止证据。
- [ ] Topic 必须处于已完成 Stage-2 的 Phase 2；不伪造旧 Phase Run 或旧 accepted，新 Stage 3 使用合法原流程。

**测试 seam：** 构造删除记录同结构的合成旧 fixture，使用真实临时 Git/worktree、原包只读验证和真实多进程锁。验证旧 runner 持锁拒绝、旧 C 先持锁导致快照陈旧、晚到旧命令被新 guard 拒绝、旧原始字节保存和 current 切换后的只读核验。对本地接管、ledger CAS、回执和消费各间隙故障注入；断言同一工作区、零重设计/重复出版、一次消费与一次派发。

**完成边界：** 旧范围为空与非空漏文件都能建立新交接；仅使用合成测试记录，绝不写真实删除记录或宿主历史。02 的活动修订是接管之后的普通功能，在 04 做组合验证，因此本 Ticket 只被 01 阻塞。对应资源闭包随切片更新。
