# 01 — 接管完成后继续阶段并独立归档旧任务

**What to build:** Controller 验证旧交付、后继接管和必要停止条件后立即完成交接，随后自动尝试一次精确旧任务归档。归档成功、明确失败或未知都不改变业务交接；Stage 1 能在正常权限、Phase Run 和停止条件下完成并进入 Stage 2。贯通控制状态转换、原持久记录、discussion/Stage Dispatch/C 调用、阶段提示、包契约和现有测试，不仅提交底层结构。

**Blocked by:** None — can start immediately.

Status: ready-for-agent
Lifecycle: completed
Publication: published to local tracker; implementation completed on isolated branch; final integration pending
Review ID: archive-handoff-decoupling-tickets-20260924-01
Spec: 旧 Codex 任务归档与阶段交接解耦，已批准 solution review archive-handoff-decoupling-solution-20260924-01；本特性 PRD。

本 Ticket 交付正常持久响应下的完整用户路径；Ticket 02 完成同一协议在中断、乱序与冲突条件下的恢复。两项均完成才可宣称本特性交付，不把中间版本部署给旧业务运行。无需独立预重构：已有 transition、ledger transaction、checkpoint 与 history 足以承载本次行为，不增加新抽象层。

## Acceptance criteria

- [ ] 保留后继准备/创建/激活所需双槽；旧交付接受且精确接管及安全证明成立时，同一次原子提交保存不可路由的 retired_handoffs 并提升后继。归档成功不再触发提升。
- [ ] 退休记录完整保存旧 ref/attempt、plan 与 entry_authority、已接受 delivery/digest、需求身份、后继及停止证明；只存在顶层，不授予旧任务写权限。正常下一阶段 start 保留历史摘要及可恢复原权威的现有历史证据。
- [ ] 首次持久交接后自动尝试归档一次；intent 先于宿主调用保存，effect 带精确 handoff_id/operation_id/ref；正常回执分别落为 archived、明确无写入 failed 或 unknown。失败/未知提示简短且不阻塞后继业务，不新增后台重试。
- [ ] 在真实临时 ledger/Git 集成路径分别注入 failed 与 unknown：Stage 0→1 接管后当前 root 为 Stage 1、临时槽已移除；Stage 1 更新需求、claim/receive/accept/finalize，并按正常授权与就绪条件进入 Stage 2。
- [ ] 旧任务在交付冻结、接管后及归档失败后均不能更新需求。missing/过期/错误 ref 的停止证据、有未决业务调用、未完成 Phase Run、未接受交付、无阶段授权都仍阻止相应不安全操作；归档不作为停止证明。
- [ ] 后继 creation pending/unknown/failed、未 claim/ready/activate 时保留原已接受成果及精确 attempt，不报完成、不自动替代或重复创建。正常取消/合法新尝试保持原语义。
- [ ] 正常重复 successor-ready、相同归档回执均 ACK，任务创建、提升和初次归档调用不重复；错误 handoff/op/ref 与矛盾选择器拒绝。未完成的业务控制事务仍阻塞，不笼统跳过 archive 名称的所有事务。
- [ ] 无旧 dedicated 的单槽、standalone 1→2、普通 native 2→3→4 保持原职责；不为 native Agent 新增侧边栏归档，不改变 Flow Worktree 和 Stage 4 清理。
- [ ] 同步控制协议、相关阶段提示、构造/校验和 fixtures：control compatibility=6、control schema=4、workflow_progress=v10；其余独立版本按批准 Spec D7 保持。新旧不兼容包交换明确拒绝；旧记录不修改、不迁移。
- [ ] 从维护源通过既有构建流程生成包并完成一致性检查，不手改生成副本；本 Ticket 不安装、部署、真实归档或更换旧固定包。

## Traceability and testing seam

覆盖 Spec D1–D4、D5 的首次操作与正常响应、D6 的正常持久提交及 start 保留、D7–D8；验收 A1、A2、A4、A7、非故障路径，A3 的正常幂等基线。主 seam 是现有 discussion_protocol 临时项目的完整 0→1→2，补充现有纯控制和 progression 的正常持久路径；宿主事实用受控适配器注入。观察授权、当前 carrier/stage、调用次数与恢复身份，不镜像实现结构。

先取得原双槽阻塞的失败回归，再通过新路径；运行受影响控制/discussion/progression 测试、兼容拒绝测试及包构建验证。真实宿主现场尚未验证，不将 fixture 回执声称为真实停止或归档。

## Comments

- 实现提交：`f1ac8e5583d511a65eb6f4d6b4a8bbbbaa080716`。完整 `bash scripts/validate.sh` 退出码 0；804 项测试通过、1 项跳过。真实宿主归档与停止回执仍未现场验证。
