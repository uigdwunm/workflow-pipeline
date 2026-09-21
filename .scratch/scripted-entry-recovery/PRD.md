# 脚本化入口证据与异常恢复衔接

Status: ready-for-agent

## Problem Statement

宿主项目与 Flow Worktree 不同时，当前入口把注册上下文与执行 cwd 混在一起；runner 虽传 registry_input，子角色未被机械约束读取同一来源。异常后，现有控制脚本能核验停止身份与部分已接受字节，上层却只允许“无进展、干净 HEAD”替换，未提交成果、替换前快照和重复恢复缺少完整衔接。

本 Spec 以冻结需求的六项可观察验收为 AC1–AC6（按原顺序）。保留当前已实现的需求交付、前台宿主多轮、原身份调用对账与审查优先最终验证。

## Solution

沿现有 runner → carrier → 原生阶段角色 → 入口预检链传递可核验的注册上下文，工作区继续独立按 Git 绑定核验。恢复优先继续原身份；只有真实停止、调用对账、成果归属和 Controller 决策齐备时，现有控制记录才准备并完成 Stage 3 接管。宿主丢失且没有真实恢复能力证据时，给出明确缺项并保留现场，不承诺恢复。

## User Stories

1. As a Workflow Controller, I want registration context to follow the original host project, so that a Flow Worktree does not hide enabled Skills.
2. As a solution designer, I want inherited evidence to be checked by the entry script, so that prompts do not become a registry substitute.
3. As an Implementation Dispatcher, I want the actual cwd checked independently, so that correct registry evidence cannot authorize another checkout.
4. As a Closure Agent, I want the same pinned package route, so that registry refresh cannot upgrade an active run.
5. As a Controller, I want stale and foreign-project evidence rejected, so that a fallback directory cannot bypass the gate.
6. As a Controller, I want a running original role to be waited on, so that recovery never creates a second writer.
7. As a Controller, I want a resumable original role continued with fresh evidence, so that accepted work stays attached to its identity.
8. As a Controller, I want unresolved calls reconciled before any followup, so that an uncertain effect is not repeated.
9. As a dispatcher, I want committed and uncommitted progress preserved, so that an interruption does not erase useful work.
10. As a Controller, I want byte ownership and drift checked, so that identical contents do not manufacture authorization.
11. As a successor dispatcher, I want exact remaining and revalidation scope, so that inherited tests are not mistaken for current candidate validation.
12. As a Controller, I want a recovery attempt to survive another interruption, so that retry cannot create two successors.
13. As an operator, I want a missing original host to remain visibly blocked, so that stopped proof is never presented as resumability.
14. As a reviewer, I want deterministic regressions separated from real-host evidence, so that substituted transport does not overstate delivery.

## Implementation Decisions

### D1 — 入口上下文的所有权与 Interface

Entry adapter 拥有注册上下文与执行上下文的交叉校验；runner 拥有 confirmed registry_input、冻结 package identities 和原始宿主上下文；Stage transfer/dispatch 与各阶段 bootstrap 只传这些已核验引用。现有 registry、registry_input、host.project_path 和 Flow Worktree binding 继续使用，不创建第二个路径配置源。

现有 registry 只有 source 与 entries，不能证明属于哪个项目，也没有对应查询回执，因此增加一个伴随的 registry_context 对象是必要接口变化。它包括 project_path、project_id、controller_ref、receipt（当前可信查询回执）与 registry_digest。project_path 必须来自原始宿主项目证据；registry_digest 绑定当前完整 registry；receipt 是可信宿主/Controller 的本次查询证据，不是任意字符串自证认证。脚本核验结构、关联与一致性，调用者仍负责宿主回执真实性。

runner 的 confirmed 保存原始宿主项目身份及 registry_input 引用；输入文件提供 registry 和 registry_context，每次启动、resume 及子角色动作前重新读取并核验。引用传给 carrier 与原生角色，由统一入口读取，不让 Agent 手工摘取 entries。入口新增 registry_input/registry_context 的受限输入组合：文件模式由脚本读取这两个对象，内联模式必须成对提供 registry 与 registry_context，二者不能混用。无委派的同目录直接调用仍可用实际 cwd 查询；跨目录、被委派的 Flow 入口缺证据必须失败，禁止回退查 Flow cwd 或另一个项目。

host.project_path 表示实际任务执行 cwd，继续等于实际 cwd；注册项目另外从继承的原始宿主证据验证，而不把工作区伪装成宿主项目。入口返回 registration_context（核验后的来源、项目、回执、摘要）和原有 repository/worktree 事实，均进入原 evidence digest 与原 checkpoint。注册项目与 binding.repository 同属已确认项目身份，实际 cwd 与 binding.worktree 精确匹配；项目 ID、controller、来源链或 Git common dir 不符就拒绝。

刷新 registry 只更新当前 callability 证据，不改变 package pin、需求、角色或授权。verify 比较冻结宿主身份和绑定，允许带新真实回执的同项目 registry 更新；旧 expected 中保存的 registry_digest 必须与当时证据自洽，不得拿旧快照覆盖当前文件。过期的操作证据指与当前输入 digest/receipt、当前调用或冻结身份不符；不发明无法证明新鲜性的 TTL。宿主不能给出当前完整证据时阻塞。

### D2 — 原执行者优先与恢复分支

Progression 是唯一下一步选择者，runner 不另建状态机。顺序为：原包/入口/授权 → 原调用对账 → 当前身份与停止屏障 → Git 现场/归属 → 原身份继续或显式接管。停止/取消意图优先；已开始发布只能对账并完成原发布。

| 事实 | 允许下一步 |
| --- | --- |
| 原角色 running | 等待原身份；禁止 followup 与替换 |
| 原角色 idle 且真实可续接、调用已对账 | 同身份继续；business_block 必须先由原 Controller 的 recover-business 解除 |
| 原角色 stopped 且宿主证明同身份可续接 | 原授权有效且无未解调用后续接；停止本身不构成可续接证明 |
| 身份 unknown、失联或存在 unresolved_action | 返回具体待查询身份/调用及缺项，保留原意图，不创建替代 |
| 原角色不可续接，所有旧写入者已停止，原宿主仍能派发 | Controller 可按 D3 准备 Stage 3 接管 |
| 原前台 owner/host 丢失 | 保留 await-host-recovery；列出原 host、carrier、native refs、未决调用、缺少的原身份恢复/完整停止证据；不启动新 app-server 冒充旧宿主 |

不新增跨宿主自动接管能力。真实宿主没有提供恢复接口时，这是已定义的产品边界。纯脚本能够测试“有可信证据时准入”和“缺证据时阻塞”，不能把替身成功算成恢复旧宿主成功。Stage 2/4 不借用 recover-dispatch 更换角色；遵循已有同身份恢复与发布清理路径。

### D3 — 有未提交成果的受控接管

复用 control/recover-dispatch，并在同一个 handoff_progress 内增加 bounded recovery 槽与历史回执；不建立恢复文件库或另一本账。Git adapter 负责实际 HEAD/index/工作树快照，control 负责授权与状态，C 负责 durable 意图、原生调用及回执对账。

增加 prepare-dispatch-recovery 动作：输入原 Controller 决策 reference、旧 dispatcher/attempt、完整停止回执引用、已对账调用引用与原因；由 C 核验真实来源，不接受 child 自报 stopped_refs 当证明。Git adapter 拍摄 HEAD、base/target、index，以及全部 allowed/protected paths 的存在性、内容 hash、文件类型/模式；检查全量实际 diff（含未跟踪相关文件）没有越界，保留原现场，不自动 stash、reset 或 commit。结果为 recovery_id、snapshot_digest、归属分类、remaining_paths、revalidate 和缺项。失败不产生派发权限。

归属分三类：已 accepted 的 Execution Agent 字节必须仍匹配原完整结果指纹；已停止但未接受的 allocation 保持原归属并全部重验；dispatcher 自身的在范围内未提交字节由 Controller 对精确快照明确承接。与 allocation 冲突或来源未知的修改阻塞，不以内容相同补齐归属。现有 accepted content hashes 不覆盖模式和存在性；恢复必须使用已有 Git allocation/result fingerprints 扩展检查这些维度。保护路径或越界修改只报告，不清理。原提交保留并验证 ancestry/scope；未提交成果不要求为了接管先造一个提交。

Controller 对准备结果的 snapshot_digest、原授权 digest、原 attempt、余下范围作明确恢复决定；普通技术恢复不新增最终用户审批门，但扩大范围、权限或改变需求仍回到原有用户决策。C 在外部创建前持久化该 recovery_id 的唯一 dispatch intent。替代角色先以受限准备态创建，禁止写入，收到真实 ready ref 后再调用 recover-dispatch 完成绑定与激活。创建结果未知则对账同一 intent，禁止再次创建；确定 not-created 才能按原意图重试。激活前重验停止、调用、快照和授权，任何漂移都保留现场并阻塞，已创建 successor 仍无写权限。

recover-dispatch 消费 recovery_id、snapshot_digest、Controller decision 与真实 replacement_ref，原子替换唯一有效 dispatcher，保留旧 ref/attempt 与执行记录，明确新 attempt；返还已接受范围、未接受待重验范围和剩余范围。历史 allocation 不再次派发；其待重验结果必须通过原归属验收或在停止且 Controller 明确承接后释放为新 allocation。相同请求 ACK 原回执；同 ID 不同 ref/digest/决定拒绝；重复调用不能重复激活或把后续 block 清除。若控制结果落盘中断，继续使用现有 C control transaction journal 对账。

### D4 — 候选与验证证据

恢复不授予 candidate、review 或 final-validation 通过状态。已有测试只作为诊断记录；只有现有验证协议证明 candidate、expected target、plan digest、源快照、命令/cwd、原始来源及完整 successful attempt 全部匹配才允许复用原最终结果，否则作废对应验证并重跑。未提交字节即使内容相同也不是完整候选证据。接管完成的实现继续按当前 reviewable → 双轴 review-converged → validation-start/result → delivery-ready 顺序；全量测试只在审查收敛后的最终验证执行。

### D5 — 兼容、范围和同步

不增加 ADR：这些是 ADR-0007 的单 Controller、单写入者、既有 checkpoint，以及 ADR-0009 的前台宿主边界内的协议补齐；不推翻“宿主丢失停止推进”。更新 originating-task 的干净/no-progress 唯一例外，使其引用 D2/D3，保留正常 checkpoint 与 remediation 的同身份路径。同步入口/包执行、transfer/dispatch、progression、Stage 2/3/4 bootstrap 与恢复说明，去掉会引导目录回退或手写恢复记录的矛盾表述。

新增必需的 durable 证据改变兼容契约，实施时统一递增受影响 entry/control/progression/runner/transfer 协议及生成 manifest compatibility key；同一新运行不能混用旧协议。已有同目录直接 preflight 调用保持支持；旧运行仍只用原 pinned runtime，不迁移、不填猜测字段、不编辑历史记录。生成包只通过现有构建器同步，不手改生成副本。范围仅为入口证据、恢复准入及相关文档测试，不重写 lifecycle/validation 系统。

### D6 — 变更契约预检与真实调用链证据

在冻结需求提交 e87726e 的生产代码上检查，非仅 mock 推断：

- runner 的 confirmed 校验保存 registry_input；_check_current_registry 重读 registry，_stage_prompt 把引用与 package_identities 传 carrier；未绑定注册项目/查询回执。这是 D1 新伴随证据的原因。
- entry adapter resolve 从实际 os.getcwd 读取 Git，硬性要求 host.project_path 等于 cwd；registry 缺失时把该 cwd 传 skill preflight。显式 registry 直传但缺项目关联。target flow 单独调用 binding verifier。D1 保留后者并修复前者来源缺口。
- stage dispatch 的 prepared/received/accepted 通过 expected_entry 与 handoff refresh 进入相同入口，再由 checkpoint 调 workflow_control_git；因此只改 runner 提示不足，必须贯穿 transfer 与 native bootstrap。
- control recover-dispatch 当前只消费 stopped_refs/file_hashes/replacement_ref，核验所有绑定旧写入者已停止和 accepted 内容 hash，返回 remaining_paths/revalidate 后直接替换 ref。Git adapter 负责 actual diff 和 hash；尚未冻结恢复前完整快照，也没有唯一恢复 decision/attempt 回执。D3 补齐此现有 seam。
- C control transaction 在调用 B 前写 request/port/result 槽，recover-dispatch 成功只清除对应 business_block、撤销旧 host proof。这些幂等/清除规则继续复用，新增 recovery 不绕过它们。
- runner _resume_owned 在原 transport/sessions 存在而旧 owner 不在时保留 await-host-recovery；原生宿主生命周期已有同进程多轮，但无旧宿主恢复证据。D2 明确保持此安全边界。
- originating-task 替换段只允许 terminal stalled、clean HEAD，与 recover-dispatch 的未提交 accepted bytes 能力不一致；D3 和 D5 在需求范围内统一。审查优先最终验证已有 control 与 Git 双重门禁，D4 直接使用。

上述 Interface 输入/输出/失败、持久化 owner、调用顺序、分支与恢复均已在 D1–D5 明确；没有把产品决策留给实现阶段。

## Testing Decisions

T1：首选现有生成包 CLI + 临时真实 Git + runner/transfer/progression 生产调用 seam，仅替换外部宿主查询/原生调用；不要 mock 掉入口预检、Git 快照、control 或 C。参考现有 entry_prepare、workflow、workflow_control_git、foreground_host、pending_host_actions、unified_recovery 与 review_first_validation 测试模式。

| 验收 | 行为与最直接可观察 seam |
| --- | --- |
| AC1 / D1 | 真实 runner prompt/transfer/native 入口请求经生成 CLI 到 preflight，宿主项目与 Flow cwd 不同；正确 registry 被使用且 Flow binding 独立验证。缺 context、项目错配、旧 digest/receipt、文件刷新、禁用依赖、pin 漂移、伪造 cwd 均在副作用前失败，无目录 fallback |
| AC2 / D2 | C advance/observe-host/control 实际调用：running 仅 wait；fresh resumable 同 ref continue；unresolved action 未决不能 followup；business recovery 仍只同身份 |
| AC3 / D2–D3 | 同宿主中旧写入者停止、调用已解后 prepare→唯一创建→bind/activate；未知/停止不足/无 Controller 决定均拒绝。host lost 返回原 refs 和缺项，无新宿主/角色派发 |
| AC4 / D3–D4 | 真 Git 中 accepted、未 accepted、dispatcher 自写的未提交修改与已有提交混合；模式、删除、新文件、index、保护路径、越界、归属冲突和 prepare 后漂移有拒绝证据；失败前后字节与 HEAD 不变 |
| AC5 / D3 | prepare、创建意图、未知创建、已 ready 未激活、control 完成未保存各中断点重入；同 ID ACK、冲突拒绝；调用数量证明无重复创建/激活/commit/publish |
| AC4–AC5 / D4 | 恢复后旧 checks 不能直接晋升 candidate；双轴审查之后才最终验证；目标/源快照/attempt 不同必须失效；完全匹配则沿现有验证协议 ACK |
| AC6 / D2 | 独立真实宿主验收记录和替身测试结果分列；有授权能力才做隔离宿主测试；宿主丢失恢复缺证据明确记 blocked/unsupported，不记 pass |

每个切片先 production seam red/green，再必要定向回归；构建生成包、仓库文档校验是受影响检查。完整仓库套件留给最终 review-converged 候选的一次 full validation；新变更或失败才重新执行。真实宿主验收复用现有隔离验收设施，禁止操作本任务以外的旧业务运行。无法完成真实 nested/host-loss 恢复时，本仓可交付严格阻塞分支与确定性回归，但报告必须注明未证明真实恢复，不升级为能力声明。

## Out of Scope

新调度器、daemon、后台服务、恢复账本；替换外部治理插件；改变宿主产品/凭据/注册配置；部署或安装；迁移旧运行或 package pin；恢复原诊断对话；自动接管失联或旧宿主；更改阶段职责；重做需求交付、生命周期和审查优先验证。

## Further Notes

需求权威：冻结文档《脚本化入口证据与异常恢复衔接》，提交 e87726eb6f68f1cc41df90db57684232fd780983，SHA-256 fa13ac356e3f11640036fa3e25e603a4fb70047461f4db19ad07b6c6002d6439。当前连续授权由本次派发承载，不修改冻结时授权描述。

原生 to-spec 测试 seam 问题：连续模式下采用 T1；无额外人类 review。ask-matt 判断为多上下文构建：需要三个 tracer-bullet Tickets，入口链、可核验接管、跨链恢复验收；各自带协议文档与行为测试。to-tickets 粒度/依赖问题由阶段预授权决定，03 依赖 01/02，01 与 02 行为独立但共享文件必须串行集成。

方案就绪检查：通过。每项行为有 owner、Interface、失败分支、状态/顺序与测试 seam；预检发现的两处矛盾已在 D1/D3/D5 解决；宿主恢复边界明确，不增加外部权限。Tickets 逐一对应 D1–D6、AC1–AC6，无独立重设计。
