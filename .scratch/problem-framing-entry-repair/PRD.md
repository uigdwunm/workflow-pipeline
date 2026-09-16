# 专用 1 拷问入口与需求写权衔接

Status: ready-for-agent
规划状态：方案与测试范围已获用户确认，可作为实现依据
需求来源：`docs/requirements/2026-09-16-problem-framing-entry-repair.md`
需求提交：`b4e48f9e6e9b771b2c2af97614c3e48b34d43357`
需求 SHA-256：`be4c3205ba4cf76c16b2dfa7ba348d64af5d4737ead3d3933115fd7486df8d27`
源码基线：`7443e7d195f2b0c559f86413be643db86b3a2605`
执行方式：用户明确允许主 Agent 代替受阻的方案子 Agent；仅规划，不实现或部署。

## Problem Statement

用户在部署前需要确信最新版 1 拷问可以实际进入并完成。当前跨阶段入口混用了 0→1 wrapper Phase Run 和只接受当前阶段的 dedicated-stage 交接；同阶段专用交接又把任务留在 accepted-awaiting-next-turn，而 Skill 要求立即提问和更新文档。遵循当前指令会遇到阶段不匹配或写权拒绝。

设计预检进一步通过真实 CLI 证实：wrapper 专用拷问合法完成 DW 写入后，claim-phase-completion 成功，但 complete-phase-run 仍用入口文档哈希检查当前文档，报 phase_source_drift。这是完成同一条拷问链路必须修复的关联缺口，不能只修启动。

## Solution

按入口选择唯一授权路线：从 phase 0 进入 1 使用 wrapper；已处于 phase 1 的任务转交使用 dedicated-stage。Workflow Controller 保留用户决策、创建回执、验收和归档，专用任务只取得限定的需求工作权限。

用户确认创建专用任务后，同阶段专用任务在验证交接与开放门禁时即可开始，不再要求额外一个“继续”。跨阶段任务依然必须等来源总控 phase-activate。普通 child 和 continuation 交接保留原有首回合只接收、后续用户触发才工作的规则。

需求输入检查点不可变；合法 DW 写入产生可核验的输出证据。拷问完成时验证输出及其来源，绝不以跳过哈希校验的方式接受漂移。执行中的 Workflow Stage 与持久话题的 Discussion Phase 分开，不提前推进 current_phase。

## User Stories

1. 作为用户，我希望从 0 讨论直接进入专用 1 拷问，不因内部阶段字段冲突而中断。
2. 作为用户，我希望已经在 1 拷问时可以转交一个专用任务，而不重复执行阶段转换。
3. 作为用户，我希望已确认的专用任务可按授权开始，不再为同一个动作补一个空的“继续”。
4. 作为总控，我希望创建回执与确切阶段授权绑定，其他任务不能冒领身份。
5. 作为专用任务，我希望每个重要结论写入同一份需求文档，并能交付实际完成的版本。
6. 作为总控，我希望专用任务写入时自己不能同时修改需求，但仍能协调门禁、取消和验收。
7. 作为用户，我希望门禁关闭时不能开展实质工作，恢复由新的用户触发，不自动轮询。
8. 作为用户，我希望取消或失败的任务不能继续写入，重复请求也不重复创建任务。
9. 作为用户，我希望普通拆分子话题仍保留接收与开始工作的边界。
10. 作为总控，我希望合法需求变化能完成交付，直接篡改文件或过期证据仍被拒绝。
11. 作为用户，我希望新阶段接管成功后才归档旧任务，恢复不丢失已完成的拷问内容。
12. 作为实现者，我希望通过真实 CLI 链路验证行为，而不是仅凭 Skill 文本检查通过。

## Implementation Decisions

### D1. 入口路由和职责

| 场景 | 唯一阶段/写权来源 | 获得写权限的时点 |
| --- | --- | --- |
| 已验证话题处于 phase 0，进入专用 Stage 1 | 0→1 wrapper，carrier_kind=dedicated-grilling | exact carrier 完成 claim、ready，来源总控 activate 后 |
| 已验证话题处于 phase 1，转交专用 Stage 1 | dedicated-stage(stage=1) | 交接验证及门禁检查通过的 accept 事务 |
| 话题 phase 0 的专用 Stage 0 | 原 dedicated-stage(stage=0) | 与同类型 Stage 1 一致，在受检 accept 后 |
| 独立 Stage 1 | 已有认证任务创建与单一草案 write_owner | 已有独立流程；不创建讨论账本 |
| child / continuation | 原 handoff | 保留后续回合 authorize-handoff-discussion |

路由由 discussion adapter 根据已验证话题和确切 Phase Run 派生，不能从标题、自由文本、任务自称或客户端 bool 推断。跨阶段路径不再额外创建 dedicated-stage(stage=1)。未知、冲突或多重合法来源一律停止，不任选一个。

同类型 dedicated-stage 的 phase 0/1 共用受检接受行为是必要关联变化；不影响新建子话题。

### D2. Workflow Control 接通真实路线

纯 Workflow Control 继续只计算计划；discussion adapter 独占 ledger 与角色事实验证。

在准备阶段计划时冻结一个有类型的 entry_authority：standalone、dedicated-stage 或 wrapper-phase-run。它记录适用的 project/tree/topic/controller、source_phase、执行 stage 和确切 handoff/Phase Run/attempt 身份；standalone 无讨论身份。该值进入 plan digest，随后每个动作复核，不允许新 attempt 沿用旧 plan。

wrapper 的执行 stage 由已验证 to_phase=1 得出，话题 current_phase 仍为 0；same-stage 由 current_phase=1 得出。配置角色因此选择 dedicated-problem-framing。adapter 不得仅因 stage != current_phase 清空正在运行的控制上下文；只有所绑定路线已完成或明确终止，才按相应恢复/接管动作改变上下文。

- creation-result：校验真实创建回执与冻结 plan attempt，再要求 exact carrier 已由 source 的 authorize-phase-carrier 绑定（wrapper），或已有 bind-handoff 回执（same-stage）；绑定不等于开始写。
- receive：校验来源任务、plan attempt、entry_authority、路径/版本、实际 Git commit:path/hash。wrapper 接受 completion-claimed/completion-pending/completed 的确切非撤销 attempt；same-stage 接受其确切 active 交接，并在收到结果后冻结进一步写入。只登记副作用事实，不从哈希自行推导来源授权。
- cancel：仅撤销当前 entry_authority 指向的 attempt。wrapper 撤销 Phase Run 授权，same-stage 撤销对应 handoff；不得取消该话题所有历史专用任务。
- 相同交付只 ACK。收到/接受的结果不可被另一 commit/hash 覆盖；重复确认不再产生 create_thread effect。

为处理前一专用 Stage 0 尚待归档的情况，只在现有 Workflow Control checkpoint 内允许一个有界 successor_control 槽，不能递归嵌套或形成队列。旧 result-accepted/归档证据保留在当前槽；新的 Stage 1 准备和创建放在 successor 槽。请求以冻结 plan_id/delivery_digest 精确定位槽，不能按“最新任务”猜测。新任务完成真实激活后，旧槽执行既有 successor-ready → archive；实际归档确认后原子提升 successor 槽。归档 unknown/失败保留两槽，停止新的后续派发，已激活任务最多完成已授权工作。取消 successor 只撤销新路线，保留旧结果供恢复。没有旧专用任务时直接使用当前槽，不增加额外步骤。该槽使用现有 ledger 控制记录，不建立新注册表或后台运行器。

### D3. 跨阶段严格顺序

1. 由现有需求写入者完成 pending DW、处理阻塞 impacts，并发布最新 stage-entry checkpoint；总控准备 0→1 wrapper。
2. 总控据该 wrapper 准备 Stage 1 计划，合并披露进入阶段和创建专用任务；已有 stage/topic-current 拒绝偏好仍使用当前任务，不反复建议创建。
3. 用户确认后只创建一次；pending client ID 不作任务 ID，unknown 先读回，不盲目重建。
4. 总控以实际 ready task ref 调用 authorize-phase-carrier，再登记 creation-result；第二步失败时重读同一 attempt/回执，不重新创建。
5. 新任务核对冻结输入并 claim、phase-ready；总控重新验证 checkpoint、gate、impact、attempt 后 phase-activate。
6. 激活事务确定唯一需求写入者，旧写入授权失效，active Conversation Binding 仍属总控。若存在已接受的旧专用 Stage 0，按 D2 保留其交付/归档事实；禁止将未完成的旧任务静默替换。
7. 新任务通过 DW 更新同一文档，用户确认拷问完成后提交精确文档、冻结 hash，claim completion。总控 receive/accept 实际交付，再 complete/finalize；current_phase 此时才变为 1。
8. 后续 1→2 仍以当前有效 checkpoint 和正式阶段结果进入；Stage 2 ready/activation 通过后才归档本次拷问任务。

准备过程和 ready 不授予写权。阶段偏好不跨阶段继承，topic-current 按现有语义保留。同阶段转交不执行以上 wrapper 创建/claim/finalize 步骤。

### D4. 专用 handoff 接受与统一写权

accept-handoff 按 handoff.kind 区分：dedicated-stage 验证绑定、payload/source hash、阶段、唯一 eligible attempt、当前需求基线且无未决 DW；在同一 ledger 锁内重新检查 Topic Gate 后，将 attempt 与 handoff 置为 active，返回 substantive_discussion_allowed=true。child/continuation 的返回和后续回合检查保持原样。重复同一 idempotency_key 返回原结果，不重复激活。

bind 可保留准备期写权预留，防止源在冻结输入后修改；接受失败不放宽所有权，也不新建 attempt。门禁关闭返回原有 gate 错误；用户以后触发恢复时以新请求键及当前 revisions 重查同一交接。不要把失败的 accept 记录成已接受，也不要自动轮询。已处于 active 的专用任务不再调用 authorize-handoff-discussion。

state 中统一计算当前有效 requirement writer，覆盖 dedicated-stage 与 activated dedicated-grilling wrapper 两种来源。权限按 operation allowlist 授予，不能把子任务认作总控：载体可 read/prepare-topic-update/apply-document-write，checkpoint 权限沿各路线既有约定；不能 split、释放 gate、创建下一阶段、验收或归档。总控在载体持有写权时不能并发 prepare/apply 需求修改，但保留只读与控制操作。对 ready、未授权、失效 attempt、已声明完成或已冻结交付都拒绝新写入。

转交时若存在任一未决 DW，先由原所有者核对并完成或按已有恢复规则处理；不允许让新任务 apply 别人的 DW。激活、撤销、完成使用同一 ledger 锁更新可写资格，跨两条路线也不能出现双写入者。

### D5. 冻结输入和合法输出分开验证

此变更仅适用于允许修改需求的 0→1 problem-framing wrapper（current 与 dedicated 两种 carrier）；其他 Phase Run 的冻结输入校验保持原样。

入口 evidence 与 source_checkpoint_identity 永远不改。activate 时在现有 attempt 初始化 working_evidence，值取刚通过验证的实时 evidence。之后只允许真实 DW 管道推进它：prepare-topic-update 核对当前 working_evidence，冻结本次允许 mutation 对源文档及 impact 等维度的预期变化，DW 记录绑定 exact run/attempt 与 before/after evidence；apply 验证 payload、文件及预期账本状态，在写成功的同一 ledger 提交中推进 working_evidence。不能在完成时无条件重算一份哈希当作合法输出。

调用在原子文件写后、ledger 持久化前中断时，复用现有 before/after 重放能力；同一个 DW 只推进一次 evidence。不属于该 DW 的 route、coverage、dependency、coordination 变化仍视为漂移；若合法 mutation 自身关闭 gate，允许完成既有 DW 的一致性落盘，但阻止下一次实质操作与完成验收，直到现有恢复规则允许。

claim-phase-completion 要求无 pending DW、无阻塞 impact、gate 开放、输出文件与 working_evidence 一致，并冻结 output_evidence，立即关闭载体新写入资格。complete/finalize 校验这份 output_evidence 与实际当前状态及已验证交付，不再把可变输出强行比对入口 source hash。phase result 同时保留输入 evidence 和输出 evidence 的不同含义，下游 stage-entry checkpoint 使用真实完成输出，不改写旧 checkpoint。

receive 的版本/hash冻结发生在 finalize 前。finalize 自身引起的 topic revision 递增是协议状态变化，不能触发重复交付或清空已接受结果；后续 checkpoint 使用递增后的 revision 和当前文档 bytes。重复旧交付仍仅 ACK，外部文件修改、已冻结输出后变更、旧 attempt 提交均拒绝。

### D6. 错误与恢复

| 条件 | 行为 |
| --- | --- |
| phase/route/项目/总控/文档/attempt 不符 | 写前拒绝；无任务或状态替代 |
| 两种有效授权竞争、旧载体尚未完成 | 拒绝新激活；返回冲突身份供总控协调 |
| gate 在 bind 与 accept/activate 之间关闭 | 拒绝启动，保留同一准备与实际任务；后续用户触发重查 |
| task 创建 unknown/pending | 保留唯一 attempt，读回 actual receipt；不伪造 ready，不重复创建 |
| DW pending 或文件被手工修改 | 保留载体与文档，走现有 DW/漂移恢复；不刷新哈希掩盖问题 |
| 用户取消或授权撤销 | exact attempt 失去写权，保留现有 bytes 与原始结果；迟到 ready/delivery 不恢复授权 |
| 已完成交付重放 | 仅 ACK；不重问、不重提交、不重推进、不重复归档 |
| 新任务 ready 失败或旧任务归档失败 | 保留旧任务与新任务证据，恢复失败步骤；不重建新任务 |

复用已有 ProtocolError 分类；只有现有分类无法精确表达时才加最小新错误码。新控制字段为本路线所需严格类型字段，未知值拒绝；不做旧部署的状态猜测、自动迁移或身份补造。

### D7. 变更契约预检与模块所有权

| 已核对生产链 | 当前矛盾/风险 | 方案拥有者与解决 |
| --- | --- | --- |
| problem-framing → dedicated protocol → prepare-wrapper-phase-run / prepare-handoff | 0→1 与 current_phase=1 同时被要求 | Skill 入口按 D1 分流；phase_runs 保留跨阶段权威 |
| discussion workflow-control → pure transition → creation-result/receive | stage 被 current_phase 覆盖；只认 dedicated-stage | adapter 核验 entry_authority；纯控制冻结计划、有限 successor 槽；D2/D3 |
| bind → accept-handoff → state owner → prepare/apply DW | accepted-awaiting-next-turn 与立即工作冲突 | handoffs 在 dedicated 类型接受时受检激活；state 统一写权；D4 |
| phase-activate → prepare/apply DW → claim → complete → finalize | 合法输出仍与入口哈希比较 | phase_runs 与真实 DW 边界维护工作/输出证据；D5 |
| result accepted → successor-ready → archive | 旧载体不能在新任务启动前丢失 | 总控保留旧槽，真实接管后归档；D2/D3 |
| child/continuation → later-turn authorize | 不能随 dedicated 修复一起放宽 | 保留原路径和对照测试；D1/D4 |

维持 ADR-0001 的讨论/Git分离、ADR-0006 的门禁边界和 ADR-0007 的总控/载体分工。方案是现有架构的入口与授权衔接修正，无需新增独立架构决策或系统级调度抽象。

## Testing Decisions

最高 seam 使用真实 discussion_protocol.py JSON CLI，临时 ledger/需求文档和真实临时 Git；只替换外部任务创建/归档回执，不 mock owner、Phase Run、DW、hash、gate 或 Workflow Control adapter。純控制 JSON CLI 仅补充 plan schema、重放和单槽/有界双槽转换检查。文本契约检查只验证指令对应正确路线，不能替代链路测试。

| 测试 | 行为与验收 |
| --- | --- |
| T1 跨阶段完整成功 | phase0 checkpoint → control prepare/confirm → 一次任务回执 → authorize/creation-result → claim/ready/activate → 至少一次真实 DW → 真实 Git 文档提交 → receive/accept → complete/finalize；确认 current_phase 仅最后变为1 |
| T2 同阶段完整成功 | phase1 dedicated prepare/bind/accept 后首回合可实际 DW 写入，不调用 later-turn authorize，不创建0→1；phase0同类型专用任务作参数化覆盖 |
| T3 身份与独占 | wrong task/attempt/topic/path/hash、双路线竞争、总控/旧载体并发写、未授权/未ready/未active 写均失败且无额外副作用 |
| T4 门禁与 pending DW | gate 在确认后或绑定后关闭、未决 DW、impact 阻塞；拒绝 accept/activate/新写/完成，后续合法用户触发恢复同一 attempt |
| T5 取消与恢复 | pending/unknown 创建只读回；取消后迟到结果不恢复；撤销后不能写；重试只恢复失败步骤和同一文档 |
| T6 输入输出证据 | 合法 DW 可完成；原 input checkpoint 不变；文件手改、非本次DW账本漂移、过期 working/output evidence 均拒绝；故障注入验证文件已写/账本未落盘的重放 |
| T7 普通交接不回归 | child/continuation 首回合不能写，后续更大 turn + gate 检查后才能写；专用修复不绕过这些分支 |
| T8 旧任务接管归档 | 已接受的专用Stage0 → Stage1 pending槽 → activate → successor-ready → archive；unknown/failed只恢复归档；新旧身份不混，不能嵌套第三槽 |
| T9 完成与下游 | claimed之后不能再写；finalize revision变化不破坏已接受回执；重复交付ACK；最新Stage1 checkpoint可准备1→2；current-problem-framing合法DW也能完成 |
| T10 文本与全量 | 两个入口逐句对应动作顺序，独立Stage1不创建ledger；原全量测试及新增测试全部通过 |

先让 T1/T2/T6 在旧代码上失败，再按同一实施上下文完成路线与接收、写权、输出验收及文档接线，最后跑全量。已有 dedicated-stage、wrapper、topic dependency 与 Workflow Control 集成 fixture 是先例。

实际验证记录：基线 335 项全量测试通过；两处入口错误已复现；本次设计又复现合法 DW 后 complete-phase-run 的 phase_source_drift。没有声称本方案的预期行为已经实现或通过测试。

## Out of Scope

部署、安装目录同步、外部治理插件修复；实现本方案；新建后台调度或注册表；一般模型策略；普通child/continuation启动语义变更；stage2–4业务重构；旧状态迁移；扩大为全库安全或性能整改。

## Further Notes

### 实施文件范围与证据定位

以下路径相对仓库根，精确分工供实现者定位。允许修改限于兑现 D1–D7/T1–T10，不能借此进行一般重构。

| 路径 | 责任 |
| --- | --- |
| skills/problem-framing/SKILL.md | 两种入口与独立入口分流 |
| skills/problem-framing/references/dedicated-grilling-protocol.md | 实际创建/授权/完成顺序 |
| skills/problem-framing/references/templates.md | exact route、authority、输入与输出交付字段 |
| skills/design-discussion/SKILL.md | dedicated 接受语义及旧任务接管衔接 |
| skills/design-discussion/references/lifecycle-integration.md | 0→1/同阶段与输出证据规则 |
| skills/guided-implementation/references/workflow-control-protocol.md | entry_authority 和有界接管语义 |
| skills/design-discussion/scripts/discussion_core/workflow_control.py | 真实授权来源核验、阶段映射与接管持久化 |
| skills/guided-implementation/scripts/workflow_control.py | 纯控制计划、schema、双槽上限及幂等 |
| skills/design-discussion/scripts/discussion_core/handoffs.py | dedicated accept受检激活；普通路径不变 |
| skills/design-discussion/scripts/discussion_core/state.py | 两类载体单一写权与操作白名单 |
| skills/design-discussion/scripts/discussion_core/phase_runs.py | 跨阶段授权、撤销、working/output evidence及完成 |
| skills/design-discussion/scripts/discussion_protocol.py | 真实DW准备/应用绑定与证据原子推进 |
| skills/design-discussion/scripts/test_dedicated_stage.py | 主成功链、同阶段、身份/接管/重放回归 |
| skills/design-discussion/scripts/test_discussion_protocol.py | wrapper/DW完成及普通handoff对照 |
| skills/guided-implementation/scripts/test_workflow_control.py | 严格字段、计划冻结和有界接管测试 |
| skills/design-discussion/scripts/test_repository_validation.py | 必要的Skill路线契约检查 |

优先复用已有测试 fixture；若需要更直接的门禁用例，放入上述集成测试，避免为了组织方式扩展范围。schema字段影响的既有消费者须在上述调用边界内同步；若发现必须改变范围外接口行为，返回方案调整，不自行扩大。

### 规划与实施边界

本工作可以在一个实现上下文中完成。依据 ask-matt 的单上下文分支，不拆 Tickets；完整 Spec 即实现与测试依据。按 to-spec 使用项目本地 Markdown tracker；用户已接受方案与测试 seam；本 Spec 标记为 ready-for-agent，通过 publish-planning 发布到 main 并保留同一 Flow Worktree。是否进入 3实现另由用户决定。

审阅 ID：problem-framing-entry-repair-review-1
审阅结果：用户在当前任务明确回复“确认”，接受方案及测试范围，并授权本地规划发布；未授权进入实现。
恢复位置：本 Spec 为完整实现与测试依据；规划提交和合并身份由 Git 及当前任务发布回执记录。
