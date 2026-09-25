# 旧 Codex 任务归档与阶段交接解耦

Status: ready-for-agent
Publication: Spec published to local tracker; implementation entry is not authorized
Review ID: archive-handoff-decoupling-solution-20260924-01
Requirement: docs/requirements/2026-09-24-archive-handoff-decoupling.md
Requirement commit: 85f7efc7500f2ef5853d3fa212b41146e5abea7b
Requirement SHA-256: 1ec0b2ef43bc9edd320edd704c9cf8900c477982f40c142f6e7b8bc4ce919443
Planning carrier: 本仓库 Local Markdown；Spec 和两个 Tickets 均已获用户确认并发布到当前方案分支，作为完整规划集统一提交。

## Problem Statement

旧可见 Codex 任务已交付、新任务已接管，旧任务也已失去需求写权限后，侧边栏归档失败仍可能阻塞后续阶段。已定位的路径是附着讨论的独立 Stage 0→独立 Stage 1：旧控制上下文保留在 root，Stage 1 在 successor_control，只有归档成功才提升 Stage 1。归档失败或未知延长双槽，使本来满足正常条件的 1→2 无法继续。

归档是侧边栏整理。它既不证明实际写入者已停止，也不拥有阶段授权。将它与交接完成绑定，混淆了两种责任。本设计不声称普通 2→3→4 都存在同样的双槽缺陷，也不取消任何停止、未决调用、需求身份或 Phase Run 检查。

## Solution

Controller 在已接受旧交付、核验后继精确身份和输入、必要停止条件及附着激活后，提交交接完成。这个持久事务同时保留旧交付及归档恢复身份，并在有 successor_control 时立即提升后继；归档不再参与提升。

交接持久提交后自动尝试归档一次。失败或未知只简短提示“交接已完成；旧任务尚未确认归档”，保持后继业务可推进。后续用户触发恢复时使用原旧任务的精确记录；未知结果先查询，得到明确未归档才允许再写。无定时重试、队列或后台服务。

## User Stories

1. 作为 Controller，我希望接管验证通过后交接完成，以免侧边栏整理阻塞工作。
2. 作为用户，我希望旧任务自动归档一次，以减少手动整理。
3. 作为用户，我希望归档失败提示简短且准确，以区分业务成功与整理未完成。
4. 作为 Stage 1 的使用者，我希望旧 Stage 0 归档失败后仍能完成 Stage 1 并按正常授权进入 Stage 2。
5. 作为需求所有者，我希望旧任务交付冻结和接管后的写权限撤销保持有效，以免旧任务继续改需求。
6. 作为 Controller，我希望旧任务仍在运行或有未决业务调用时停止交接，以免两个写入者并发。
7. 作为恢复操作者，我希望保存原旧任务、原创建 attempt 和已接受交付，以精确恢复。
8. 作为恢复操作者，我希望未知归档先查询原任务，以免盲目重复操作。
9. 作为 Controller，我希望重复接管和归档回执只确认既有结果，以免重复创建或提升。
10. 作为 Controller，我希望晚到旧归档回执只更新旧证据，以免当前 Stage 2 上下文被覆盖。
11. 作为 Controller，我希望冲突身份或冲突回执失败关闭，以免误归档当前任务或替代任务。
12. 作为用户，我希望后继创建/激活失败保存原成果与精确尝试，以便恢复而非重做已接受交付。
13. 作为恢复操作者，我希望交接提交前后中断都有明确恢复位置，以免丢证据或重复提升。
14. 作为旧运行的使用者，我希望升级不改写我的包和记录，以便由原运行时继续恢复。
15. 作为维护者，我希望沿用已有 ledger/checkpoint 与锁，以免引入第二个状态真相。
16. 作为维护者，我希望 standalone 和普通 native 下游路径保持其既有边界，以免扩大本次修复。

## Implementation Decisions

### D1. 责任与真实调用链

Workflow Control 纯状态机拥有上下文校验、精确选择、交接提交和归档证据转换；它不调用宿主。discussion adapter 拥有附着讨论的授权核验及持久事务。Stage Dispatch 的 checkpoint 是纯控制/Git 验证与 discussion mutation 的路由边界。Workflow progression（C）保存宿主调用、原请求和因果回执，驱动调用及恢复。Controller/宿主适配器证明实际停止与归档事实。Git adapter 只证明 Git，不证明任务停止。

真实生产链为 Controller/C control → Stage Dispatch checkpoint → discussion workflow-control（附着 0/1）→ Workflow Control transition → 同一 ledger 原子提交；standalone 或下游 native 的 checkpoint → Git adapter/纯 transition → 原 conversation/runner checkpoint。附着 adapter 的 successor-ready 当前以 Phase Runs 验证唯一实际激活的 carrier；纯 transition 当前在 archive-result 的 archived 分支提升 successor。C control_transactions 保存原 port，而 C start 重建当前 state 并保留 history。设计必须同时改动这些读取/写入边界，不能只移一条提升语句。

### D2. 旧证据归属：已有控制 checkpoint 内的不可路由历史

在控制 context 顶层增加 `retired_handoffs` 字典，键为 `handoff_id`。它是既有 workflow-control 记录的一个字段，不是新 ledger、文件、注册表或可执行队列。附着 0/1 的权威持久所有者仍是现有 Phase Results 中该 topic 唯一的 workflow-control 记录；standalone 的所有者仍是既有 conversation/runner checkpoint。

每条记录保存：Controller/topic；旧 stage、carrier kind/ref/attempt；旧 plan 的 plan_id 与 entry_authority；完整已验证 delivery 及 delivery_digest；旧 requirement_identity；精确 successor-ready 证据；停止/未决调用证明引用；交接完成标记；归档状态及下述操作/回执记录。可复用原冻结对象，不能只保存指向将被覆盖的 root 的引用。`handoff_id` 是 Controller/topic、旧 plan_id/attempt/ref 和 delivery_digest 的规范摘要；其身份字段不可更新。保留原 plan 足够字段核验摘要和来源，不复制活动权限或嵌套 successor_control。

现有 Phase Runs 中 dedicated attempt 的 delivery_frozen/delivery_accepted/binding_eligible 以及 wrapper attempt 的授权、输出证据继续是真实写权限与生命周期来源。retired_handoffs 是已接受交付和整理证据，不授予写权限，不参与普通 active control 选择。

历史只存在顶层，后继临时槽不携带历史副本。提升时把原顶层历史与本次旧记录一起装入新 root；同键不同冻结内容是冲突，不覆盖。保持历史到现有记录正常生命周期结束；本次不做 GC。既有大小限制触发时失败关闭并保留当前状态，不静默丢历史；不新增任意截断策略。

为何不只放 C history：交互式附着路径未必有 C，且历史 C port 是过期业务快照。为何不增加 Phase Result 行或清理队列：同一控制 checkpoint 已拥有精确计划和接受证据，可在一次现有提交里保存与提升，无跨记录事务协议。C history 保留原宿主与事务审计，但不成为另一份归档状态权威。

### D3. 交接完成与状态转换

保留 prepare/decide/creation-result/receive/accept 及准备期间的双槽语义。在 `successor-ready` 校验全部通过时完成下列一个状态转换：冻结旧记录 → 设置 `handoff_completed` → 若有临时 successor_control，将其提升为 root 并移除临时槽；归档状态初始化为 `not-requested`。在返回结果中明确给出 `handoff_id` 和 `handoff_completed:true`。不在同一纯转换里执行宿主归档。

无 successor_control 的旧可见任务也生成相同退休记录，旧当前 progress 标记 `handoff-complete`，由已有下一阶段 start 逻辑建立新活动上下文；不得凭 successor-ready 的 ref 构造一个缺失实际计划/绑定的活动控制槽。此路径不强制引入双槽。只有旧 carrier 为 dedicated-stage 时产生侧边栏归档记录；普通 native 2→3→4 继续既有 accepted/start/停止屏障，不为 native Agent 新增侧边栏归档。

| 前置状态/事实 | 结果 |
| --- | --- |
| 旧交付未接受、输入/角色/身份不符 | 拒绝；不记录交接、不提升、不归档 |
| 后继创建 pending/unknown/failed、未实际绑定或未激活 | 保留旧接受结果和原后继 attempt；走原查询/恢复，不报完成 |
| 必要停止证据缺失或有未决业务调用 | 阻止交接，保留原恢复位置 |
| 精确后继已准备并激活，全部安全条件满足 | 原子保存退休记录及提升；业务交接完成 |
| 同一 successor-ready 重放且冻结证据一致 | ACK；不再次提升、不创建任务、不重复自动归档 |
| 同旧身份的新 successor-ready 指向不同后继/交付/证明 | 拒绝冲突；原记录、当前 root 不变 |
| 归档失败、未知、未请求或仍待查询 | 仅整理状态；不阻止后继正常 accept/start |

已有 wrapper completion 对接受证据的读取扩展为“当前/临时槽或精确退休记录”，按 run_id/attempt_id/delivery_digest 定位，要求恰好一致的已接受证据；不因找不到当前槽而跳过核验。更新允许的终态包含 handoff-complete。旧需求授权检查仍由其原记录负责，历史对象从不进入 writer roster。

### D4. 停止与未决调用的责任

继续区分协议冻结、权限撤销、实际停止：receive 冻结旧 dedicated 写权限；激活新的可写需求阶段仍在 ledger 内核验旧 delivery_accepted 并撤销 binding_eligible；实际进程/宿主活动由原适配器证明。归档成功、completion footer、空任务列表或人工填入 stopped=true 均不等价于停止证明。

C/Controller 在可写后继激活前核验原旧写入者的精确宿主终止/停止证据和全部相关未决调用；successor-ready 提交前再次验证证据未被后来 running/调用事件失效。纯控制接口新增结构化 `takeover_proof`：旧 ref/attempt、原宿主/适配器身份、因果 stop receipt 及 invocation/response 引用、已对账的业务调用集合摘要。C 从已保存的原始宿主状态派生，拒绝调用者冒充派生字段；交互式入口由已认证 Controller 读取真实工具回执并传入同等事实。纯函数仅验证结构和交付身份，不能声称认证任意字符串；discussion adapter 检查 owner、原 attempt、真实激活、冻结/撤权事实。协议须明确这条信任边界。

保留 C start 的 accepted、stopped、stop_barrier_pending、unresolved_host_actions、verify_phase_completed、pending decision、需求/包固定身份检查；保留 B/allocation 未决事务与业务 control_transactions 对账。取消/替代后继继续走现有身份与授权恢复，不能用归档失败证明可替代。

仅已持久化的侧边栏归档操作按 D5 单独分类为整理调用；它不属于业务写入者或阶段执行调用，不阻塞业务。未知 successor-ready ledger 提交、未知写权限撤销、宿主/业务调用未决仍阻塞。不能笼统忽略所有 action 名含 archive 的事务，更不能把尚未明确落盘的交接事务当已完成。

### D5. 精确归档接口、操作身份和回执规则

归档动作从活动槽路由分离。`archive` 必须提供 handoff_id；`archive-result` 必须提供 handoff_id、operation_id、旧 ref、status 和已认证 receipt 身份。旧 control_plan_id 如同时提供必须与该历史记录一致；不允许缺少历史选择器时回退到“当前 carrier”。这些动作只读写命中的退休记录。未知 handoff_id、多个矛盾选择器、当前任务 ref、替代任务 ref 或不属于记录的 operation_id 一律拒绝。

每条退休记录内部保存顺序归档 operations（已有记录的审计字段，无调度行为）：operation_id、kind（archive/read-archive-state）、精确旧 ref、序号、原因/前序操作、请求提交状态及原始回执引用。先持久化 intent 再交宿主；宿主必须记录精确调用 ID、响应 ID、适配器、实际请求 ref 和 raw 响应。重复返回 effect 必须沿用 operation_id，不意味着允许再次调用宿主；C/交互适配器查原调用，明确无调用才派发。单个归档目标一次最多一个未决操作。

首次成功交接由 Controller 自动调用 archive；重放交接只返回已有交接标识。调用已经 issued 但无确定结果、requested/unknown、超时、传输失败一律先查询原 ref；不能重新发 archive。宿主明确报告未发生写入或 readback 证实 not-archived 后，用户触发的重试可创建新 archive operation。明确失败若不能证明无写入，也归为 unknown。查询失败/未知保留未确认状态，不产生循环；等待下一次按需恢复。

| 回执情况 | 更新与下一步 |
| --- | --- |
| 同 operation/receipt 身份、完全相同字节 | ACK，不重复 effect |
| 同 receipt 身份内容不同、operation/ref 不符 | 拒绝并保留原证据，走原 Controller 恢复 |
| 当前操作 archived | 该目标归档完成；以后 archive 为无操作 ACK |
| 查询明确 not-archived | 允许后续一次显式重试，创建新 operation_id |
| archive 明确失败且无写入 | 保存 failed，允许按需重试 |
| 未知结果或不能证明无写入的失败 | 保存 unknown，后续只可查询 |
| 已终结旧操作晚到相同结果 | 仅 ACK，不改变较新状态 |
| 已终结旧操作晚到新/矛盾内容 | 保存冲突证据并拒绝应用，要求精确 readback；不逆转 archived 或当前业务 |
| 较早已发出的 archive 成功在后续查询后才到达 | 不按时间猜先后；保存为待对账证据，由新精确查询确认状态 |

archived 在本功能内终结；本次不实现 unarchive。用户从外部取消归档后的重新整理属于新的明确操作，不伪装成旧成功回执失效。所有失败/冲突均不切换 stage、不恢复旧 carrier、不重新执行旧 accept 或 successor-ready。

### D6. 持久化、跨阶段与中断恢复

附着场景在原 ledger 短锁、revision 和 idempotency_key 下把交接历史与新 root 一次写入；原 event result 保留提交回执。独立场景由既有 checkpoint 原子替换保存完整返回 context，提交成功之前不执行归档。不得先单独提升后写历史。

C 保存的 control_transactions 继续拥有交接业务事务的原请求与原 mutation envelope。若 ledger 已提交但 C 未保存响应，重放原 envelope 得到原 idempotent result；须完成该对账再 start。若恢复时当前业务上下文已经更新，原返回 context 只能用于核对 handoff_id/既存提交，不能覆盖新上下文。无法证明先后与一致性则走现有 control_context_changed/recovery，不能自行回退。

归档调用复用现有 `control_transactions` 保存 intent/原始回执/应用结果，并增加明确的 `scope:archive` 及 handoff_id/operation_id；业务事务仍为原 scope。归档分支不把冻结旧 port 用作业务恢复快照；其中原 discussion envelope 只定位旧 topic/owner 及精确原提交，不授权覆盖当前业务。`unresolved_control_transactions` 对归档项仅排除已经明确提交交接、且持久化为整理操作的项；其余未知控制事务继续阻塞。应用归档结果时取得权威最新 context，仅对其退休记录转换并提交；附着历史始终通过其原 discussion 身份和最新 revision 读写 ledger，不能把进入 Stage 2 后的 native port 当成该 topic 的归档权威。C 的 control_transactions/history 是调用审计与恢复路由，最新 ledger 为附着归档真相；standalone 的当前 context 为真相。

C start 已将完整旧 state（除 history 自身）追加到 history，因此旧 control_transactions 并未丢失。本次复用这一历史，不新增 archive_transactions 或另一套 history。新增只读精确查找：在当前 state 及各历史快照中按 Controller/topic、handoff_id、operation_id、原调用/响应 ID 定位交易；相同冻结请求的重复审计副本视为同一交易，身份相同内容不同则拒绝。优先使用明确因果引用的最新应用结果，不能以数组最后一项或任务时间推断目标。跨 start 的晚到结果作为当前 control_transactions 的归档项记录，引用原交易；原历史快照不就地改写。新的 operation 必须接续权威退休记录已确认的前序 operation，历史中孤立记录不能自行发起写入。

当前控制 context 中退休摘要在 start 时显式保留；原附着归档 authority 路由和完整宿主调用从上述既有 history 精确恢复，不额外复制一份路由表。附着进入 native 下游后，context 中已携带历史仅作不可写的审计投影，所有归档变更回到原 ledger；返回原 ledger 的完整 control 不能覆盖 native 当前 context，仅合并已验证的对应退休记录投影。进入下游时若输入 context 缺失已有退休证据，必须从原权威读取并保留，不能接受遗漏历史的替换；不跨 topic/Controller 合并。晚到归档回执在当前阶段处理，响应携带当前 context 与该 handoff 整理结果，绝不把旧 port 作为当前业务状态。

中断点：提交前无变化，可重放原交接；提交后响应丢失按原事务对账，提升只发生一次；归档 intent 已提交但未调用可查调用证明后派发；已调用结果丢失先 readback；已在 Stage 2 的旧归档结果仅更新退休证据。后台不存在唤醒者，持久未完成状态本身不主动重试。

### D7. 兼容与发布边界

| 契约 | 当前 | 本次选择与理由 |
| --- | --- | --- |
| control compatibility key | 5 | 提升至 6：提升时机、归档选择器及停止证据语义不兼容 |
| control context/request schema | 3 | 提升至 4：历史字段和归档请求形状改变；与 compatibility 数字独立 |
| workflow_progress | v9 | 提升至 v10：整理事务持久字段、跨 start 保留和晚到结果应用规则改变 |
| stage_transfer | v6 | 保持：B 的 sealed handoff/dispatch/delivery 结构不变；checkpoint 调用更新 control schema 常量，由 control=6 和完整兼容键隔离 |
| runner | 6 | 保持：外层 runner schema/宿主生命周期无变化；内嵌 C 独立版本已提升，并受固定包身份约束 |
| discussion_request / ledger_read / ledger_write | 1 / [1,2,3] / 3 | 保持：原 mutation envelope、ledger 表与事务格式不变，变化位于已有 data_json 的显式 control schema；旧 context 由 schema 门禁拒绝 |
| handoff / entry / supervision / thread_settings / preparation / requirement_delivery | 当前各版本 | 保持：角色交付、Git 生命周期、模型设置、需求准备/交付协议不改 |
| package release/source_revision/bundle_digest | 1.1.0 系列 | 在获准实施并正常发布时一致更新实际包身份；本方案不发布版本，不伪造 digest |

所有 schema 构造/校验及测试 fixture 必须一致更新，不能仅改清单。新运行五个包使用同一完整兼容键；混用任一旧/新包明确拒绝。新运行时读取旧 control 或旧 C 返回 legacy_run_requires_original_runtime，保留字节不自动补字段；旧包读取新记录也必须拒绝，不猜测降级。正在运行的旧包路径/digest/registry pinned 身份、runner/ledger 内旧 control 不迁移；继续原运行时。升级不会自动解除旧运行的归档阻塞，这是有意的安全边界。

维护只改 src、构建清单及相关测试/协议；生成的 skills 由既有构建流程统一生成并检查，不能手改。无需新增 ADR：方案延续 ADR-0001/0007/0008/0009 的状态归属、Controller 责任、固定包和停止边界；更新现有控制协议阐明归档不决定交接。必要时给 ADR-0007 的现有归档句增加澄清，不改变其架构决策。

### D8. 变更契约预检与范围

已逐项核查接口、持久所有者、前置顺序及冲突，并作如下决策：当前只允许 archived 提升与需求冲突，按 D3 改为交接提交；旧身份依赖活动 root 与晚到回执安全冲突，按 D2/D5 使用非活动历史；C 原 port 重放与跨 start 更新冲突，按 D6 分离整理事务并应用到最新权威；纯 bool 无法证明停止，按 D4 保留宿主责任并核验引用；版本号有多个独立层，按 D7 只提升实际改变的契约。

实现模块范围包括控制状态机及 Git adapter 的 schema 传递、discussion workflow-control/phase completion 对历史证据的读取、Stage Dispatch schema 传递/最新归档权威路由、C control/start/validation/view 与事务恢复、受影响阶段模板与共享协议、兼容清单与构建生成验证、上述行为测试。仅当现有 writer 校验需要识别新历史字段时调整其读取；不改变其授权规则。保留 Stage 2/3/4 原 native 生命周期、Flow Worktree 与最终发布职责。

## Testing Decisions

用户已连同方案确认的测试 seam：以现有 discussion_protocol 临时项目集成 seam 为主，走真实 ledger、Git 需求提交和 Phase Run；宿主归档/停止回执用受控适配器注入，测试外部结果与持久重启。补充已有纯控制 seam 做严格回执组合，已有 progression seam 做跨 start 与丢响应恢复。无需新后台或真实账户测试 seam。

好测试观察当前 stage/carrier、唯一 task-create/归档调用、授权拒绝、保存后的恢复结果和错误码；不逐字段镜像内部实现。注入停止与归档证据不能被描述为真实宿主验证。

| 验收/分支 | 最直接测试及可观察结果 |
| --- | --- |
| A1 归档 failed/unknown 后 1→2 | 改写既有双槽集成用例：0→1 接管后立刻 root=1、无 successor_control；分别失败/未知，Stage 1 改需求、claim/receive/accept/finalize，并以有效 Stage 2 授权完成准备/接管；旧归档保持未确认 |
| A2 旧无写权限 | 在接管前交付冻结及接管后、归档失败后分别尝试旧 prepare-topic-update 均拒绝；新 Stage 1 正常更新；晚到回执不能恢复旧权限 |
| A2 安全条件 | 缺/过期/错误 ref 的 stop、未决业务调用、未知交接事务、未完成 Phase Run、缺授权分别阻止正确边界；仅归档 unknown 不阻止 |
| A3 幂等/冲突 | 同 successor-ready 重放 ACK；同回执重复 ACK；错误 handoff/op/ref、矛盾选择器、同 receipt 不同内容拒绝； task-create 与提升次数始终为一 |
| A4 后继失败 | 创建 unknown 查询原 attempt；failed/取消保留接受成果；未 claim/ready/activate 拒绝交接；新合法 attempt 不继承旧回执 |
| A5 精确恢复 | 进入 Stage 2 后对 Stage 0 handoff 恢复：先查询 original ref；not-archived 后才写同 ref；不调用当前 Stage 2 或替代 ref；多个历史 handoff 独立处理 |
| A5 时序 | issued 无响应、readback unknown/failed、晚到旧 success、相同/冲突 raw、终态重复均覆盖；无自动 retry loop |
| A6 原子恢复 | 注入 ledger 提交前、提交后响应前、C save 前、archive intent 后、归档宿主响应前中断；重启原 checkpoint 对账，不丢 delivery，不重复提升/写调用 |
| A6 跨 start | Stage 1 accepted→Stage 2 start 后旧 archive-result 不重放旧 port；当前 stage/carrier/要求身份和已完成交付不变；历史/原 authority 路由保留 |
| A7 版本 | 旧 schema/C 拒绝且文件字节不变；新旧包兼容键不等时拒绝；生成包的构建检查通过；runner=6/B=v6 的新包正常使用新 control/C |
| 非故障路径 | 无旧 dedicated 的单槽 0/1、standalone 1→2、普通 native 2→3→4、取消/替代及原 Stage 4 清理回归保持；不为 native 创建侧边栏归档 |

实施验证顺序：先上述失败回归，再控制/discussion/progression 定向测试，再项目规定的完整验证与包构建一致性检查。真实宿主现场只在另获授权的隔离测试任务中验证 readback/归档 raw 映射与停止证明；不以真实业务任务作测试、不运行部署。本次仅完成静态方案，不声称已复现宿主故障或测试通过。

## Out of Scope

实现、安装、部署、真实归档；迁移/替换旧运行及其固定包；后台服务、自动重试/清理队列、通用状态系统；模型选择、Stage 3 角色精简、runner 收缩；重做所有阶段控制；归档替代停止证明；Stage 4 文档/合并/清理行为变更；新增自动 unarchive；不必要的历史压缩与 GC。

## Further Notes

用户已明确确认 Review ID archive-handoff-decoupling-solution-20260924-01 的完整 Spec 与 Testing Decisions；同一 solution_designer 已按 to-spec 发布至本地 tracker 并标记 ready-for-agent。该状态不替代进入 Stage 3 的独立授权。ask-matt 决策：需要 Tickets，正常交接链路与中断/晚到恢复适合分成两个可独立验收的上下文切片；用户已明确接受 Tickets review archive-handoff-decoupling-tickets-20260924-01 的两个切片和 01→02 依赖；两 Tickets 已按 to-tickets 发布为 ready-for-agent。当前未实施、未安装或部署。

工作区基线：隔离任务工作区，分支 codex/archive-handoff-plan，HEAD 与 Requirement commit 相同；初始 clean，草稿路径原不存在。用户批准使用当前工作区替代 start-worktree；未创建第二层 Flow，未伪造标准 Worktree Binding。用户批准跳过宿主 Skill 注册检查；没有伪造 registry、A/B/C 或宿主成功回执。source_commit 与 delivery_commit 均为上述需求提交；已核验工作树与 committed bytes 同一 SHA-256。需求文件只读。

源码证据索引（固定到上述基线；此索引用于审阅定位，不是实现路径约束）：

- `src/shared/scripts/workflow_control.py`：validate_context、validate_progress、selected_control、transition、successor-ready/archive/archive-result；原提升位于 transition 尾部。
- `src/shared/scripts/discussion_core/workflow_control.py`：workflow_control 的 Phase Results 保存、entry authority、successor 激活验证、delivery freeze/accept 与原 ledger 事务。
- `src/shared/scripts/discussion_core/state.py`：writer roster、delivery_frozen 检查、_write_ledger_transaction/_persist_ledger/_atomic_replace。
- `src/shared/scripts/discussion_core/phase_runs.py`：_check_completion_evidence 与 phase-activate 的旧 binding_eligible 撤销。
- `src/shared/scripts/stage_dispatch.py`：checkpoint 与 prepared 的控制端口/双槽选择。
- `src/shared/scripts/workflow_progress.py`：start、validate_state、control_action、unresolved_control_transactions、require_business_ready；保存/重建与 history 边界。
- `src/shared/scripts/workflow_control_git.py`：宿主事实与 Git 事实的职责分界。
- `tests/runtime/test_dedicated_stage.py`：test_accepted_stage_zero_successor_keeps_both_slots_until_archive / run_wrapper_control_delivery 与 test_real_modified_delivery_survives_phase_advance_before_archive。
- `tests/runtime/test_workflow_control.py`：test_accept_then_successor_ready_then_archive_and_reconcile。
- `src/shared/references/guided-implementation/workflow-control-protocol.md`、`src/shared/references/workflow-progression.md`、`build/skill-packages.json`：公开调用、双槽约束和独立兼容版本。

方案就绪检查：模块所有者、接口与信任边界、重要分支、持久状态与恢复、验收 seam 均已覆盖于 D1–D8 及 Testing Decisions。未运行实现测试、构建、宿主故障复现或实际归档。Spec/测试 seam 已确认；Tickets 已独立确认并发布；规划交付后停在 2方案边界，不重复需求冻结、规划发布或 Agent 启动。
