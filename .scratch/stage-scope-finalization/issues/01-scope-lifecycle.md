# 01 — 设计后封存范围并完成正常阶段交接

Status: ready-for-agent
Lifecycle: completed
Review: tickets-review-20260926-01 — accepted
Blocked by: None — can start immediately after planning authorization
Spec: ../PRD.md — D1、D2、D3、D6；Testing Decisions
Requirement: ../../../docs/requirements/2026-09-26-stage-scope-finalization.md

**What to build:** Stage 2 可以在后续文件未知时启动；设计完成后由设计者提出完整精确清单、Controller 核对封存，正常发布和接受规划，再为 Stage 3 生成可验证就绪交接。interactive、runner、standalone 与附着讨论采用同一范围权威。这个切片必须能走通一条完整正常交付路径，不是只加 schema 或纯校验函数。

实现采用 Spec 已定的 shared stage_scope、C 持久事务、B 验证和原控制权威。模块不自行授权、持久化或派发。新普通协议版本、资源声明及相应生成闭包随本切片同步，原普通旧运行入口继续拒绝不兼容状态；旧窄恢复由 03 完成。无需先做独立重构。

- [x] null 未定、明确空和非空清单严格区分；manifest 数组投影一致，每个文件有 owner、operation、kind、依据，生成物有 source_paths。
- [x] 需求、目标、规划权限与同一 Flow Worktree 在启动时冻结；设计者不因提出清单获得实现写入权限。
- [x] 完整候选提交产生后构造机器 manifest，不在 Spec 中制造自身 commit 哈希循环；缺依据、遗漏已声明生成输出或保护/所有权冲突在出版前拒绝。
- [x] scope-propose / scope-seal 绑定原 Controller、需求、候选、target、binding、revision；同 ID 同内容 ACK，异内容或陈旧决定拒绝。
- [x] implementation_required=true 且空范围不能出版/就绪；false+空可完成 planning-only，保留工作区且不派发 Stage 3；明确空 closure 合法。
- [x] 规划出版仍只有一次，target 前移与未知出版结果沿用原 reconciliation，不以换候选或重新接受绕过。
- [x] planning_complete 与 handoff_ready 分离；附着讨论走“B 接受规划 → Phase finalize → C readiness”，不存在循环前置条件。
- [x] Stage 3 prepare/render 验证 C readiness 和 B 接受配对；无 readiness 的直接 B 调用不能启动实现。分配只可缩小当前 ceiling，不能丢失阶段完整集合。
- [x] 独立 Stage 3 使用自包含冻结依据封存范围，不强制新增 Stage 2；Stage 4 保留完整保护规则和空归档清单行为。
- [x] 连续模式不新增用户审批；范围内细化由 Controller 核对，业务扩张仍要求原变更决定。

**测试 seam：** 真实 C→B→临时 Git/worktree/checkpoint；附着分支使用真实 Topic ledger；runner 使用现有 host fixture。断言出版/派发次数、真实路径拒绝、源 bytes/mode 不变及当前 readiness。覆盖 Spec 的未定/空/完整、越权与漂移、正常出版、独立/连续分支。纯模块测试只补必要组合，不能取代生产调用链测试。

**完成边界：** 对应运行测试与五包必要构建闭包可通过；尚不实现活动 scope-revise 或旧接管。继承 Spec 的精确文件上限与六项旧机械缺口，未经解决不得实际启动实现；不安装、不改真实业务记录。

## Implementation Notes（2026-09-27）

正常路径已在源码接入：Stage 2 未定范围、逐文件 manifest、Controller 封存、独立的规划完成/交接就绪投影，以及 B 的当前 readiness 核验。实施基线 e24136f 已先行使用 progress-v13/transfer-v8/control key 7/runner outer 7，本切片当前实现改用 v14/v9/key 8/outer 8，嵌入 control JSON schema 仍为 5。`test_collaboration_lifecycle.py` 单文件 20/20 已通过；包构建与全量回归仍待总体验收。本 Ticket 的验收清单保留未勾状态，最终逐项依据集成结果核销。


## 集成验证收尾（2026-09-27）

源码与五包已同步，完整回归及失败夹具复测、补充测试的合并覆盖为 58 个模块、1,000 个用例（999 通过、1 个既有跳过）；初次全量和后续复测的区别见 PRD 实施记录。构建一致性、repository validator 与 diff 检查通过。未安装或接管真实业务记录。
