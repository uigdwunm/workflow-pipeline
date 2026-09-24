# 02 — 中断或晚到回执后精确恢复旧任务归档

**What to build:** 用户在已进入后续阶段后仍能按需恢复原旧任务的归档。未知结果先查询；中断、重复、乱序或冲突回执均保留原交付，不重复提升或归档，也不覆盖当前阶段。贯通原权威读取、现有事务/history 精确定位、宿主查询/写入意图和用户可观察恢复结果，完成批准 Spec 的全部故障边界。

**Blocked by:** 01 — 接管完成后继续阶段并独立归档旧任务。

Status: ready-for-agent
Publication: published to local tracker; ticket review accepted; Stage 3 entry not authorized
Review ID: archive-handoff-decoupling-tickets-20260924-01
Spec: 旧 Codex 任务归档与阶段交接解耦，已批准 solution review archive-handoff-decoupling-solution-20260924-01；本特性 PRD。

依赖 01 的 handoff/operation 身份、原子提升和新版本契约；不另建归档事务表、history、调度服务或恢复 registry。沿用同一集成分支完成，保持既有正常路径测试通过。

## Acceptance criteria

- [ ] 从当前 control_transactions 与既有 history 按 Controller/topic、handoff_id/operation_id 和原调用/响应 ID 精确查找；冻结相同的审计副本归为同一交易，同身份不同内容拒绝。不按任务标题、recency、数组末项或替代任务推断身份。
- [ ] Stage 1 accepted→Stage 2 start 后，对 Stage 0 旧记录恢复归档：附着路径回到原 ledger 权威，独立路径用当前 checkpoint；只更新对应退休证据/审计投影，当前 stage/carrier、需求身份、已完成交付及活动权限不变。绝不以旧 port 覆盖当前 context。
- [ ] issued 无响应、requested/unknown、传输失败或不能证明无写入的 failed，下一次按需恢复只查询原 ref；查询明确 not-archived 或明确失败无写入后才允许新归档 operation。查询失败/未知不循环，不自动创建下一写调用。
- [ ] 多个历史 handoff 和当前活动任务同时存在时，各自 operation 互不混用，原旧任务恢复不会操作当前任务、替代任务或兄弟 topic。缺少原权威、包或身份依据时保留状态并走原恢复，不猜测或迁移。
- [ ] 相同 operation/receipt 字节重放只 ACK；同 receipt 不同内容、错误 operation/ref、矛盾选择器、已终结旧操作的新结果保留冲突证据并拒绝应用。晚到旧成功不按到达时间覆盖较新状态，按 Spec 要求重新精确查询。
- [ ] archived 是本功能终态，后续相同归档无操作 ACK；冲突证据不逆转业务或 archived，不实现 unarchive。重放交接不重复 task-create、提升或初次归档。
- [ ] 注入 ledger 提交前、提交后响应前、C save 前的中断：重放原 idempotency envelope 对账，同一交接最多提升一次，旧完整 delivery 不丢失；未知业务提交未对账前不放行 start。
- [ ] 注入归档 intent 持久化后/宿主调用前、调用后/响应前中断：证明未调用才派发；已调用但结果未知先查询。原 invocation/response 与 intent 因果关系保留，不能把重复 effect 当新调用授权。
- [ ] 归档事务仅在已确认交接且其整理 intent 持久化后可从业务阻塞集合排除；未决宿主业务调用、B/allocation、停止屏障或其他控制事务仍阻塞，旧停止证明被新运行事件失效时不得复用。
- [ ] 原历史快照不就地改写；晚到结果记录在当前现有事务集合并引用原交易，缺失/冲突历史失败关闭。既有大小上限触发不静默截断旧证据。
- [ ] 完成 Spec Testing Decisions 的所有分支及正常路径回归、项目规定完整验证和生成包一致性检查；再次证明旧 schema/C/固定包记录拒绝且字节不变。不得安装、部署、真实归档或进入新范围。

## Traceability and testing seam

覆盖 Spec D2 的历史不可变性、D4 的安全/整理分界、D5 全部重试与回执矩阵、D6 全部中断及跨 start 恢复、D7 的保留原运行时；验收 A3、A5、A6，补齐 A2/A4/A7 的恢复负例，回归 01 的 A1。沿用现有 progression 持久重启 seam 和 discussion_protocol 原子 ledger seam 注入提交边界/丢响应，纯控制 seam 覆盖严格回执矩阵；不引入新宿主系统。

观察原旧 ref 的 readback→条件写入顺序、确切调用次数、唯一提升、错误码、旧交付完整性及当前业务上下文不变。真实宿主现场验证另需明确授权；本 Ticket 的受控故障注入不代表已验证真实账户工具行为。
