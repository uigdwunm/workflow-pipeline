# 审查收敛后执行最终全量验证

Status: ready-for-agent
Lifecycle: completed
Review: solution-review-20260920-01（用户已确认方案及 Testing Decisions；连续执行后续全部流程）
Requirement: docs/requirements/2026-09-20-review-before-full-validation.md
Requirement commit: 0c535aab872eda0db0bfd2784de0358cf42f29d0
Requirement SHA-256: 638425cde48dae398ce085c60281fefd1ba0d31725d4d5be3018754253727707

## Problem Statement

尚未通过独立审查的实现版本先运行昂贵全量验证，修复后重复同样工作。此前完整测试约 28–31 分钟；这是成本背景，不是本方案的性能承诺。目前文档要求候选先完成 focused/full，而脚本只要求非空 checks/tests，既不能区分可审查和可交付，也不能证明必需全量实际完成。

## Solution

采用同一个 Stage 3 内的顺序：实现及定向/受影响检查 → 干净可审查提交 → Originating Task 独立 Standards、Spec 审查 → 冻结候选的最终验证 → Originating Task 接受可交付结果 → Stage 4 归档发布。审查修复只运行对应回归和受影响检查，先审查新提交；最终全量及必需昂贵现场验收在审查收敛后执行。

保留一个 Flow Worktree、原 Implementation Dispatcher、原 Controller 和原生角色身份。验证清单及证据放在既有控制检查点；C 保存的宿主活动和 B 的交付仍按原职责工作，不增加账本、Workflow Stage、测试执行器或缓存。完整验证的结果不能由非空字符串列表、模型 completed 声明、引用路径或摘要替代。

## User Stories

1. 作为实现者，我希望完成定向和受影响检查后即可送审，避免对会被审查修改的代码先运行全量。
2. 作为审查者，我希望两轴共享准确提交和目标分支基线，避免审查不同版本。
3. 作为 Originating Task，我希望修复回到同一调度者和工作区，保留已有实现和来源权威。
4. 作为用户，我希望审查通过后才开始昂贵最终验证，并看到可审查与可交付的区别。
5. 作为交付者，我希望缺失、失败、跳过或错误提交的必需验证都拒绝进入 Stage 4。
6. 作为维护者，我希望失败记录不可覆盖，未改变代码的重试能够说明原因和独立结果。
7. 作为维护者，我希望最终失败导致代码变化时，重新进行两轴审查及最终验证。
8. 作为用户，我希望暂停或取消优先于下一次验证派发，迟到的成功不能越过停止门禁。
9. 作为用户，我希望进程中断后保留原身份、调用和证据，未知结果不会被当作成功或自动重放。
10. 作为归档者，我希望目标分支变化会让旧固定点失效，并回原调度者处理。
11. 作为归档者，我希望合法归档文档提交不冒充已验证的实现提交，也不导致无关全量重复。
12. 作为旧运行的操作者，我希望新包明确拒绝升级旧记录，旧运行继续由原固定包恢复。
13. 作为包使用者，我希望独立安装包、脚本和 Skill 说明使用一致的门禁。
14. 作为验收者，我希望生产链回归区分确定性传输替身证据与真实宿主能力证据。

## Implementation Decisions

### 1. 所有者与调用边界

- workflow control 拥有唯一 Stage-3 业务状态、验证清单和提交绑定的结果校验。提取同一模块内的可审查校验、两轴校验和可交付校验；所有消费者复用，不复制宽松分支。
- Git control adapter 拥有真实 HEAD、工作区清洁、作用域、保护来源和目标分支核验；绝不相信调用者 clean 或 commit 声明。
- workflow progression（C）拥有原生调用、审查活动、事务落盘与停止优先；新增业务动作仍通过既有 control-action/transaction 通道记录，不建立第二权威副本。
- stage dispatch/handoff（B）仅接受真正可交付的 completed；可审查候选是继续执行中的业务进度，不能占用不可替换的最终 delivery，也不触发完成 Phase Run。
- foreground runner 继续驱动 C 返回的 next_action，保留原 foreground host。它不直接执行测试，不以 carrier 自述 completed 绕过 B/C。
- 原 Implementation Dispatcher 执行测试命令并报告原始结果；Originating Task 拥有独立审查、审查收敛确认、最终接受权；Closure Agent 只做归档文档、集成及清理。

### 2. 最小证据契约

在 start-dispatch 时将 Spec/standalone brief 中的 testing basis 解析为冻结 validation_plan，并由 Controller 接受。清单包含非空 review_required 和 final_required 集合；每项包含唯一 id、category（focused、affected、full、environment）、实际命令或明确验收程序、执行 cwd 的绑定、通过条件、允许的既有 suite 内 skip 及环境身份要求。final_required 至少有一项 full；需要真实环境的项目再加入 environment。项目不要求现场验收时保留清晰的不适用依据，不创建假通过条目。

清单的语义由原 testing basis 和仓库要求确定，digest 只标识这些已确认字节。执行者不能自行删减失败检查、改命令或把 required 改成 optional；清单变更走既有计划异常。focused/affected 允许一个实际检查覆盖两个目的，但必须显式映射；不使用自动影响分析。

本仓的 final_required 包含完整 validate.sh，以及本次变更所需的真实单层生命周期与 stop/pause/resume/cancel 验收；既有真实嵌套集成保持外部依赖待验证，不列为已通过或暗中提升为新阻塞。真实验收以候选构建包进行，不安装到用户活动目录。

每个候选记录准确 candidate_commit、expected_target_head、binding、scope/source 身份、plan_digest、定向检查记录及 review。两轴各记录 candidate、expected_target_head、独立 reviewer_ref、accepted 状态和原审查结果引用；C 中对应的原生调用必须有真实 ref 和 stopped 证据。Controller 的 review 收敛决定绑定这整组证据的 digest。

最终 verification 保留 plan 本体或可校验的冻结清单投影、plan_digest、candidate、expected_target_head、review_digest、attempts。每次 attempt 在调用前记录唯一 attempt_id、必需清单、原 dispatcher_ref 和状态 pending/running；完成记录每项 check_id、category、命令/程序身份、cwd、开始/结束观察的 commit、退出码或验收判定、passed/failed/skipped/unknown、环境指纹与原始输出引用及其摘要。原始结果来源必须由现有受信宿主/Controller 工具观察认证；digest、模型写的 pass 或日志路径本身不认证执行。

最终通过要求最近一次已明确发起的完整 attempt 终态 passed，所有 final_required id 恰好覆盖一次，无 running/unknown/失败必需项，所有结果及审查指向同一候选、目标和计划，前后 Git 核验均符合约束。failed/skipped 不等于 passed；套件内已有允许 skip 可按冻结通过条件接受，但整个必需检查 skipped 永远失败。本仓现有历史 skip 只能按事先列明原因保留，不能用测试总数或退出码掩盖新 skip。

这是一份执行结果契约，不增加测试引擎：脚本校验覆盖、来源、顺序与身份；原调度者继续运行项目现有命令。结构正确不证明日志语义真伪，Controller 仍负责读取真实输出并确认语义，不能降低已有权限和来源核验。

### 3. 状态与转换

Stage-3 handoff_progress 在现有 implementing 分支中扩展内部状态；不是新的 Workflow Stage。candidate 旧语义改由以下明确状态取代。

| 当前状态 | 动作与必要证据 | 新状态 / 结果 |
| --- | --- | --- |
| implementing | candidate-ready：原调度者提交全部范围，定向/受影响必需项通过，所有分配停止并接受，Git 干净且目标固定 | reviewable；downstream_ready=false |
| reviewable | C review-activity 启动两个独立角色，使用同一 candidate/target/plan | reviewing；仅收集审查 |
| reviewing | Controller review-converged：两轴 accepted，无未解决项，原生身份/停止与 Git 重验通过 | final-validation-pending；持久化 review_digest |
| reviewing | Controller remediation：有行动项，旧审查者停止且旧调用已对账 | implementing；保留历史证据，清空当前可交付资格 |
| final-validation-pending | validation-start：准确固定点未变、没有停止/未知调用、原 dispatcher 可继续；先保存 attempt 再发同身份 followup | validating |
| validating | validation-result：受信原结果，全部 required 满足，Git 未变 | deliverable；仍须原 Controller/B 正式接受 |
| validating | 明确失败或完整检查未完成 | final-validation-failed；保留每项原结果，不自动宣告 completed |
| final-validation-failed | Controller 指向失败 attempt 的明确重试决定，代码/目标/计划未变，旧测试已结束且宿主可继续 | final-validation-pending；创建新 attempt，完整重跑 final_required |
| 任一未发布状态 | 实现修改、候选 HEAD 变化或合并新目标 | implementing；旧证据仅作历史，重新定向检查、两轴审查、最终验证 |
| deliverable | 原生完整停止门禁 + B receive/accept 重验 + Controller 接受准确 delivery | Stage 3 accepted；允许 Stage 4 |

每个新动作均验证 actor、原 carrier attempt、binding 和当前状态。candidate-ready 可重复 ACK 相同字节；相同 identity 不同内容拒绝。任何候选替换必须明确 remediation/invalidate，不能覆盖最终已接受 delivery；已进入 Stage 4 则走既有 Stage-3 recovery 路径，保留原 dispatcher/Flow。

C review-activity prepare 消费真实 reviewable 状态和 review verification，不再接受任意非空 checks。观察 stopped 只证明角色停止，不等于 accepted：review-converged 必须有 Controller 的语义结果与对应原生响应。validation-start 前的收敛顺序由持久化状态证明，时间戳不能替代顺序。

B 的 completed Stage-3 payload 保留 candidate_commit/review/verification 字段，内部验证改为严格新契约并与控制检查点一致。B receive 不再将任何 completed 反向制造 candidate-ready 或验证通过；只有 deliverable 才能固化最终 delivery，receive/accept 均重新核验。reviewable 通过 control-action 和普通 continue 观察传递，C 输出审查动作时不意外继续写代码。最终验证仍通过既有 continue-host 精确 followup 给原 dispatcher，不创建新角色。

### 4. 失效、重试与停止的优先级

优先级保持：取消/暂停意图与原生停止屏障 → 未知调用、transport loss、未完成事务的对账 → 来源/权限/固定点核验 → 审查或验证业务动作。验证分支加入这个顺序，不能提前返回新的 next_action 绕过它。

- HEAD、源字节/模式、计划、binding、scope 或 expected_target_head 不同，当前证据不可用于交付。HEAD 相同但工作区变脏也不允许测试发起、接受或发布。若证据表明测试期间源码曾变化，即使后来恢复同 HEAD，也将该 attempt 标为失效并重新验证；无法证明未变化则 unknown。普通日志和临时输出不落入实现源码范围，验证最终仍要求工作区符合现有清洁规则。
- 目标分支推进时在 review-converged、validation-start/result、B receive/accept、Stage-4 entry 和 publication 前重验。原调度者把新目标合入原 Flow，生成 replacement commit，再做定向检查、两轴审查和最终验证。不能只把 expected_target_head 改成新值。保护来源或实质语义冲突走原异常流程。
- 全量失败但代码未变，可复用仍有效的审查；保留失败记录，以新 attempt 完整重试 final_required。不给自动重试次数策略，不拼接不同完整尝试的通过项，不回退选择早先成功覆盖较新失败。具体技术问题由原调度者诊断；反复同机制仍执行既有 diagnosis-first 规则。
- 部分结果、进程崩溃或中断为 unknown/incomplete，不推断失败后未写入或测试已结束。先对账原调用/子进程与实际 Git，已完成且可认证的原结果可幂等接收，不能重新运行未知副作用的原调用。明确未完成且原操作已停止后才允许新 attempt；先前尝试继续保留。
- 暂停冻结业务派发并保持当前证据；恢复先核对原宿主、原生角色、所有执行分配及历史审查者。取消后迟到结果只用于停止和副作用对账，不能开放下游。原宿主丢失不等于可恢复原身份；沿用既有 host-recovery-required，不自动更换 dispatcher。
- 跨暂停到达的成功可以作为待对账证据保存，不能直接把业务状态推进 accepted。最终验证与 B 事务一样保存 exact request/result 后再原子消费。验收决定只能由原 Controller 发出，不能由 executor 的 completed 文本推导。

### 5. Stage 4、归档文档及最终发布

Stage-4 start-closure、handoff 前置校验和 C publication authority 都调用同一可交付校验。Stage 3 的实现候选 I 始终是 review/verification 对应提交；归档文档候选 D 是 I 的后代，只能增加明确 closure_paths 的文档提交。现有 B candidate_commit 保持 I，publication_candidate 与 supervision candidate_commit 保持 D，不能把 I 的结果改写成对 D 的全量声明。

归档文档检查在 D 执行；比较 I→D 的完整路径/字节/模式范围，确保未改实现、测试、构建配置、生成包或保护来源。源码 Skill Markdown 和生成包说明是可执行契约，属于 Stage 3 实现范围，不能放进 closure_paths。这样合法归档记录不触发整个实现全量重跑；任何实现变化回 Stage 3。

监督 Git 发布器仍是底层 Git 原语，保留 flow-worktree-v2、publication intent/facts、target_changed 和 cleanup-only 语义；业务证据在进入它的 Stage 4/control/C 边界检查。原始 complete-worktree 不被宣称为独立的审查认证服务。standalone closure 继续仅有文档范围，没有 Stage-3 候选时不伪造新的最终验证证据。

已发布事实优先于重做测试或修复请求：发现已 merge 时只对账/清理/完成原 Phase Run，不返回未发布重试，不生成第二次 merge。未发布 target_changed 则按上节重新形成可交付结果。

### 6. 兼容边界

这是严格门禁的破坏性协议变化。新 runner record 使用 version 5，C 使用 workflow-progress-v7，B 使用 workflow-stage-transfer-v3，control request/context schema_version 使用 2；生成包兼容键中的 control=2、workflow_progress 和 stage_transfer 对应更新。其余未变的 Git supervision、ledger、preparation、thread-settings 和 requirement-delivery 协议保持原值；不做无关大版本升级或安装。

新包对 runner v1–4、C v6、B v2、control v1 的可执行恢复明确报 legacy_run_requires_original_runtime（含实际旧版本及原固定包身份），在写盘、创建原生角色和运行测试前停止，不填默认字段、不迁移旧 checks 为 passed。旧记录只用于诊断展示，原固定包继续原行为。discussion ledger 外层版本不变；已有嵌入的旧 control 不能被新包重写，须回原运行时完成；新建控制记录使用 schema 2。新旧包兼容键不相等则拒绝交接。

C/B/runner 和 attached control 的 schema 创建点、拒绝路径、测试 fixtures 统一更新。release 字符串不作为兼容证明，bundle digest 固定机制保持。正在执行本次 Stage 2 的已安装旧包不升级、不改写；未来 Stage 3 实现后生成的包只是候选产物。

### 7. 变更契约预检与矛盾消解

| 实际入口与生产调用链 | 当前证据与矛盾 | 本方案解决及可观察结果 |
| --- | --- | --- |
| foreground runner → C start/observe/advance → B prepare/receive/accept → control/Git adapter | runner v4 消费 C completion；B receive 对 completed 调 candidate；文档要求先 full，control 仅检查非空 tests | candidate-ready 与最终 completed 分离；C 不在 reviewable 时完成，B 只接受 deliverable |
| Originating Task → C review-activity prepare/observe → 原生 Standards/Spec | prepare 仅有 candidate + 非空 checks；observe 保存 ref/stopped，没有语义收敛动作 | 先核定向结果，再 Controller 收敛，两轴固定 candidate/target/plan |
| B verify_result → control.validate_review → runner Stage-3 handoff 校验 | validate_review 验 accepted candidate，但 verification 只需 checks 非空；runner 有宽松 useful-evidence 分支 | 同一严格 delivery validator 贯穿接收、接受、交接，删除弱兼容旁路 |
| B Stage-4 prepare → start-closure → Git adapter；C publication readiness → supervision | 已存在 I/D 分离、停止屏障、发布事实对账，不能合并为一个提交标记 | 保留 I 的审查/全量证据，限制 D 的归档范围并重新核目标 |
| standalone Stage 3 → start-dispatch/control → Stage 4 | 无 A requirement 时不能只依赖 B/C 门禁 | 相同 control/Git validator 和标准 handoff 字段；无平行宽松路径 |
| discussion wrapper → control → 原 ledger checkpoint | schema 1 创建点写死；版本升级可能误改旧记录 | 新建 schema 2；旧嵌入控制只诊断拒绝，不迁移 ledger |
| dispatcher 修复、目标刷新 → 新候选 | 当前多处要求 affected/full 后再复审 | 全部统一为 affected → review → final，保留 diagnosis-first |

已追踪实际调用者、状态所有者、失败与顺序；无需新增 ADR，因为沿用既有 ADR-0005/0007/0008/0009 的角色、存储、独立包和宿主决定，没有新增难逆架构。证据定位见 Further Notes。

## Testing Decisions

最高主 seam 是公开 runner/C 操作驱动真实 B/control/Git/临时仓库，沿用现有 ProgressTests 和 runner/publication fixtures；只在边界之外替换宿主传输、耗时测试命令和故障注入。不得替换本次修改的 validator、C 转换、B 交接或真实 Git 边界。少量纯 control 测试覆盖畸形输入和错误优先级；包测试验证生成闭包与跨包拒绝。无需模拟三十分钟真实等待来验证顺序。

| 需求验收 | 主要测试及断言 |
| --- | --- |
| 1 可审查不先全量 | 用真实 candidate-ready/C review-activity；focused/affected 通过就能发两轴，调用日志中 full 尚未发生；任一缺失/失败不能送审 |
| 2 修复先复审 | 审查行动项 → 同 ref remediation → 新提交 → 新两轴；旧 review_history 保留且停止屏障覆盖，修复循环没有 full 前置 |
| 3 收敛才最终验证 | review-converged 前 validation-start 拒绝；两轴通过后 exactly one frozen attempt；所有 final_required 满足才 B receive/accept 与 Stage 4 放行 |
| 4 严格拒绝 | 空/非空旧字符串 checks、缺项、重复 id、wrong category/command/commit/target/plan、untrusted result、失败/skip/unknown、wrong reviewer、review 不收敛全部 fail closed；direct B/control/runner handoff 也不能旁路 |
| 5 失败与恢复 | failed→同提交新完整 attempt 保留失败、审查仍有效；改代码→两轴和 full 必须重来；较新失败遮蔽旧 pass；重复同结果 ACK、同 id 改内容拒绝 |
| 5 暂停/取消/崩溃 | 验证请求前/派发后/保存结果前后注入中断，验证 stop 优先、原 identity、未知调用不重发、迟到结果不放行；旧审查与嵌套执行分配仍在停止屏障 |
| 5 来源与目标 | review 后、final 前后、accept 前、closure 发布前推进 target；确认原 Flow 回流、新固定点；保护来源变化停止；dirty worktree、测试中源码变化不能通过 |
| 5 归档与幂等 | I→D 只改闭包允许文档可发布；运行契约 Markdown/测试/生成包改动拒绝；merge 已完成后恢复只清理/Phase Run，不重新验证或发布 |
| 6 旧运行/包 | v4/v6/v2/control1 拒绝且记录字节不变、无 host 调用；新键全包一致；旧验证对象不能自动升级；新包仅执行新记录 |

实施顺序先做一个真实 C→control/Git 的“focused 可送审，但 completed 缺最终证据拒绝”纵向红绿测试，再扩展 review→final→handoff，随后失败/生命周期/发布和包兼容。不能先批量改所有 fixture 使测试天然全绿。

本次 Stage-2 只运行文档 diff、包构建一致性和仓库校验；不运行未修改的长时 runtime 全量。Stage 3 的最终验证在两轴收敛的准确候选上执行完整 validate.sh；真实单层 lifecycle 与 stop 用候选自包含包执行并保存 raw events/report。验证清单在实施入口冻结，真实宿主权限输入缺失则明确 blocked，不猜测或用 transport double 替代。真实嵌套集成与前次 EPERM 根因调查不在范围，既有 pending 标识保留。

实现时测试运行结果与计划必须可区分：本表是验收设计，不宣称上述测试已经运行。最终交付报告须区分自动化通过、真实单层通过、嵌套未验证，列出实际 skip 依据。

## Out of Scope

不建设测试缓存、自动影响分析、并行全量、测试引擎、调度服务、通用状态库；不换角色或降低质量；不修外部 subagent-governance、真实嵌套集成、旧下载任务和 EPERM 根因；不安装、部署、远程写入或发布版本。本次 Stage 2 不实现代码。

## Further Notes

### 生产证据与预期修改面

以下是固定基线上的证据索引和实施分配依据，不是第二份需求文档。行号以后可能移动，应按模块和符号核对。

- `src/shared/scripts/workflow_control.py`：validate_review、validate_progress、start-dispatch/candidate/start-closure；当前 review 仅严格绑定 candidate，verification 只检查 checks 非空。修改结构化验证、内部状态及 schema 2。
- `src/shared/scripts/workflow_control_git.py`：verified_transition；修改 candidate/review/final/closure 的真实 Git 固定点检查。
- `src/shared/scripts/workflow_progress.py`：review_activity、control_action、call/consume_transaction、advance、verify_publication_authority/readiness；修改 v7、业务顺序、strict acceptance，保留 stop/native lifecycle/transaction/publication facts。
- `src/shared/scripts/stage_dispatch.py`：prepared、received、accepted 与 checkpoint；修改 v3 completed 和 new control evidence 映射，移除 completed 倒建候选的弱路径。
- `src/shared/scripts/stage_handoff.py`：verify_result、predecessor、render；修改 v3 精确交接和 strict verification 投影。
- `src/shared/scripts/discussion_core/workflow_control.py`：control 创建/调用 schema 和旧嵌入记录拒绝；外层 ledger 不变。
- `src/stages/guided-implementation/scripts/workflow.py`：record v5、legacy 拒绝、carrier prompt、handoff validator；`workflow_stage_result.schema.json` 同步 completed/continue 语义。foreground_host 本身无需改传输/子树生命周期；只有生产调用确需传递新 evidence 时才做相应狭窄适配，不重构。
- `src/shared/references/guided-implementation/{execution-protocol,originating-task-protocol,workflow-control-protocol,worktree-execution}.md`、`src/shared/references/{workflow-progression,stage-transfer,package-execution}.md`、`src/stages/guided-implementation/SKILL.md.in`、`src/stages/change-closure/{SKILL.md.in,references/closure-protocol.md,references/closure-actions.md}`：统一新的顺序、状态、证据、失败回流和版本边界。这些是实现契约，Stage 3 修改。
- `build/skill-packages.json`：仅相应 compatibility key 变化；`scripts/build_skills.py` 沿用现有生成器。由实际 resource closure 重新生成所有受影响 `skills/` 文件与 manifest；不手改生成副本、不把生成包留到 Stage 4。
- `tests/runtime/test_workflow_control.py`、`test_workflow_control_git.py`、`test_workflow_progress.py`、`test_stage_transfer.py`、`test_workflow.py`、`test_attached_flow_progress.py`、`test_business_recovery.py`、`test_pending_host_actions.py`、`test_publication_progress.py`、`test_publication_recovery.py`、`test_unified_recovery.py`：按上述 seam 补行为用例和更新实际版本 fixtures；可在同目录新增主题测试文件并复用 fixtures。
- `tests/packages/test_build.py`、`test_cross_package.py`、`test_preflight.py` 和版本相关 fixtures：验证资源闭包、生成一致性与新旧兼容拒绝。`tests/host/verify_native_lifecycle.py` 与 `tests/host/README.md` 仅同步必要输入版本/本次证据，保留现有单层范围。

Stage 3 的精确 allowed_paths 应由上述源文件、必要测试及构建器实际生成清单展开为文件列表；不得直接授权整个仓库。实现中发现清单外必要模块时先按现有范围机制报告，不能以“关联修改”扩大权限。无需改监督 Git 发布实现，除非测试证明其现有 I/D 或 target 守卫不能兑现已定义行为，此时回本阶段说明证据。

### 归档范围与保护范围

Stage 4 仅更新本主题 PRD 的实施/验证/关闭记录及本主题 Tickets 的 Lifecycle；若后续 native ask-matt 决定不需要 Tickets，则只有 PRD。冻结需求保持 protected。其他主题 PRD、既有 ADR 的历史验收记录、CONTEXT 及用户配置均不在本次归档范围。必要实现契约 Markdown 与生成包不按文件扩展名误分成归档文档。

### 当前设计就绪结论

模块和接口所有者、真实调用链、状态/顺序、失败优先级、同提交证据、I/D 区别、旧记录拒绝、测试 seam 与生成包范围均已确定。没有必须留给 Stage 3 的产品/兼容/测试门禁选择。方案和测试 seam 由同一次用户审阅确认；确认后执行 native ask-matt 的 Tickets 决策，不提前进入实现。

### Tickets 决策与追踪

原生 ask-matt 按 multi-session build 路由判断需要 Tickets；已执行原生 to-tickets，以五个可验收纵向切片发布。连续模式下自行接受拆分 quiz：粒度适合独立实施上下文；01→02→{03,04}→05 是必要依赖，03 和 04 在行为上独立但共享脚本，默认由同一调度者串行整合，不能并发写相同文件。无额外 prefactor Ticket；只在第一条真实纵向 seam 中做必要局部重构。

| Ticket | 交付与依赖 | Spec / 需求覆盖 |
| --- | --- | --- |
| 01 | 可审查入口，无阻塞 | §1–3、§7；验收 1、2、4 |
| 02 | 收敛后最终验证及严格交付，阻塞于 01 | §1–3、§5；验收 3、4 |
| 03 | 失败、重试及停止恢复，阻塞于 02 | §3–4；验收 2、4、5 |
| 04 | target 回流、I/D 发布与清理，阻塞于 02 | §4–5、§7；验收 3、4、5 |
| 05 | 版本、全链及生成包最终验收，阻塞于 03、04 | §6–7、Testing Decisions；验收 1–6 |

各切片同步自己引起的版本创建点、源契约与生成副本；05 完成横贯兼容覆盖与最终整体验收，不意味着前面可以暂时开放宽松旧证据。所有切片完成才是完整实现交付。

### 精确路径交接

以下为允许修改的文件上界，不要求无差别修改。测试 fixtures 范围仅限本次版本/契约连锁变化；不授权无关重构。新增主题测试使用已列的唯一新文件名。所有 paths 均相对仓库；保护路径优先。生成路径按当前包 manifest 的 resource closure 展开，不手工编辑。

#### implementation_paths

- `build/skill-packages.json`
- `skills/change-closure/SKILL.md`
- `skills/change-closure/package.json`
- `skills/change-closure/references/change-closure/closure-actions.md`
- `skills/change-closure/references/change-closure/closure-protocol.md`
- `skills/change-closure/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/change-closure/references/shared/guided-implementation/worktree-execution.md`
- `skills/change-closure/references/shared/package-execution.md`
- `skills/change-closure/references/shared/stage-transfer.md`
- `skills/change-closure/references/shared/workflow-progression.md`
- `skills/change-closure/scripts/discussion_core/workflow_control.py`
- `skills/change-closure/scripts/stage_dispatch.py`
- `skills/change-closure/scripts/stage_handoff.py`
- `skills/change-closure/scripts/workflow_control.py`
- `skills/change-closure/scripts/workflow_control_git.py`
- `skills/change-closure/scripts/workflow_progress.py`
- `skills/design-discussion/package.json`
- `skills/design-discussion/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/design-discussion/references/shared/package-execution.md`
- `skills/design-discussion/references/shared/stage-transfer.md`
- `skills/design-discussion/references/shared/workflow-progression.md`
- `skills/design-discussion/scripts/discussion_core/workflow_control.py`
- `skills/design-discussion/scripts/stage_dispatch.py`
- `skills/design-discussion/scripts/stage_handoff.py`
- `skills/design-discussion/scripts/workflow_control.py`
- `skills/design-discussion/scripts/workflow_control_git.py`
- `skills/design-discussion/scripts/workflow_progress.py`
- `skills/guided-implementation/SKILL.md`
- `skills/guided-implementation/package.json`
- `skills/guided-implementation/references/shared/guided-implementation/execution-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/originating-task-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/worktree-execution.md`
- `skills/guided-implementation/references/shared/package-execution.md`
- `skills/guided-implementation/references/shared/stage-transfer.md`
- `skills/guided-implementation/references/shared/workflow-progression.md`
- `skills/guided-implementation/scripts/discussion_core/workflow_control.py`
- `skills/guided-implementation/scripts/foreground_host.py`
- `skills/guided-implementation/scripts/stage_dispatch.py`
- `skills/guided-implementation/scripts/stage_handoff.py`
- `skills/guided-implementation/scripts/workflow.py`
- `skills/guided-implementation/scripts/workflow_control.py`
- `skills/guided-implementation/scripts/workflow_control_git.py`
- `skills/guided-implementation/scripts/workflow_progress.py`
- `skills/guided-implementation/scripts/workflow_stage_result.schema.json`
- `skills/problem-framing/package.json`
- `skills/problem-framing/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/problem-framing/references/shared/package-execution.md`
- `skills/problem-framing/references/shared/stage-transfer.md`
- `skills/problem-framing/references/shared/workflow-progression.md`
- `skills/problem-framing/scripts/discussion_core/workflow_control.py`
- `skills/problem-framing/scripts/stage_dispatch.py`
- `skills/problem-framing/scripts/stage_handoff.py`
- `skills/problem-framing/scripts/workflow_control.py`
- `skills/problem-framing/scripts/workflow_control_git.py`
- `skills/problem-framing/scripts/workflow_progress.py`
- `skills/solution-design/package.json`
- `skills/solution-design/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/solution-design/references/shared/guided-implementation/worktree-execution.md`
- `skills/solution-design/references/shared/package-execution.md`
- `skills/solution-design/references/shared/stage-transfer.md`
- `skills/solution-design/references/shared/workflow-progression.md`
- `skills/solution-design/scripts/discussion_core/workflow_control.py`
- `skills/solution-design/scripts/stage_dispatch.py`
- `skills/solution-design/scripts/stage_handoff.py`
- `skills/solution-design/scripts/workflow_control.py`
- `skills/solution-design/scripts/workflow_control_git.py`
- `skills/solution-design/scripts/workflow_progress.py`
- `src/shared/references/guided-implementation/execution-protocol.md`
- `src/shared/references/guided-implementation/originating-task-protocol.md`
- `src/shared/references/guided-implementation/workflow-control-protocol.md`
- `src/shared/references/guided-implementation/worktree-execution.md`
- `src/shared/references/package-execution.md`
- `src/shared/references/stage-transfer.md`
- `src/shared/references/workflow-progression.md`
- `src/shared/scripts/discussion_core/workflow_control.py`
- `src/shared/scripts/stage_dispatch.py`
- `src/shared/scripts/stage_handoff.py`
- `src/shared/scripts/workflow_control.py`
- `src/shared/scripts/workflow_control_git.py`
- `src/shared/scripts/workflow_progress.py`
- `src/stages/change-closure/SKILL.md.in`
- `src/stages/change-closure/references/closure-actions.md`
- `src/stages/change-closure/references/closure-protocol.md`
- `src/stages/guided-implementation/SKILL.md.in`
- `src/stages/guided-implementation/scripts/foreground_host.py`
- `src/stages/guided-implementation/scripts/workflow.py`
- `src/stages/guided-implementation/scripts/workflow_stage_result.schema.json`
- `tests/host/README.md`
- `tests/host/verify_native_lifecycle.py`
- `tests/packages/test_build.py`
- `tests/packages/test_cross_package.py`
- `tests/packages/test_preflight.py`
- `tests/runtime/test_attached_flow_progress.py`
- `tests/runtime/test_business_recovery.py`
- `tests/runtime/test_dedicated_stage.py`
- `tests/runtime/test_discussion_protocol.py`
- `tests/runtime/test_foreground_host.py`
- `tests/runtime/test_pending_host_actions.py`
- `tests/runtime/test_publication_progress.py`
- `tests/runtime/test_publication_recovery.py`
- `tests/runtime/test_requirement_delivery.py`
- `tests/runtime/test_review_first_validation.py`
- `tests/runtime/test_stage_transfer.py`
- `tests/runtime/test_unified_recovery.py`
- `tests/runtime/test_workflow.py`
- `tests/runtime/test_workflow_control.py`
- `tests/runtime/test_workflow_control_git.py`
- `tests/runtime/test_workflow_progress.py`

#### closure_paths

- `.scratch/review-before-full-validation/PRD.md`
- `.scratch/review-before-full-validation/issues/01-reviewable-candidate.md`
- `.scratch/review-before-full-validation/issues/02-final-validation-delivery.md`
- `.scratch/review-before-full-validation/issues/03-validation-recovery.md`
- `.scratch/review-before-full-validation/issues/04-closure-fixed-point.md`
- `.scratch/review-before-full-validation/issues/05-compatibility-final-acceptance.md`

#### protected_paths

- `docs/requirements/2026-09-20-review-before-full-validation.md`

以上路径展开未改变已确认行为或权限；执行包仍使用父任务固定旧包，候选包升级只影响待交付的新运行。

## 实施、验证与关闭记录

2026-09-21：五个 Tickets 完成，Lifecycle 与 triage Status 分别记录。接受的实现 I 为 `72d37ad011671a2fcd52c69279fd8e606e55c731`；目标和作用域基线为 `eb54d378c82fe82deaf8c0651c6a218b97d35e34`。同一 Flow Worktree 承载方案、实现及六份文档归档。归档候选 D 仅更新这些文档，I→D 不修改实现、测试、生成包或保护需求。审查与完整验证仍证明 I，不改写成 D 的全量证明；最终合并和清理事实由既有 evidence root 的 `closure-delivery.json` 记录。

### 实际顺序与审查修复

先做真实生产边界的定向红绿及受影响检查，再提交两轴审查；每轮修复先定向回归再复审，审查收敛后才运行昂贵最终验证。本次运行保持固定旧包 release 1.1.0、bundle `78667077240623baa09626efa1dedce17da0d4df83600904ceacf4a894cbff7c`，未切换到新构建包。

- Standards 的 malformed collections 问题：在 membership/deduplication 前严格检查字段与非空字符串，畸形结果不再留下无法解决的事务。
- Spec 的 stale control port 问题：新业务变更前对账原 unknown control，只重放原请求并重验保存上下文。环境验收允许冻结条件明确认可的 trusted passed/null；命令仍必须实际 exit 0。
- 第二轮 Spec 发现暂停对账未继续实际交付：修复持久化恢复意图并继续原 resume 流程，回归验证到真实 B receive/accept。stopped-only 证据仍不能伪造原宿主可继续状态，已发起调用不重复派发。
- 最终 I 两轴 accepted：Standards `/root/sg_standard_review_review_first_implementation_on_t_839664837667`，Spec `/root/sg_standard_review_review_first_implementation_on_t_99c85eeaae21`。原调度者保持 `/root/sg_standard_implement_accepted_review_first_valid_t_5b4c1cf5ba0e`。诊断、红绿原始记录和完整修复列表保留于 evidence root 的 `review-r2-diagnosis.json`、`review-r3-delivery.json` 及关联日志。

### 首次失败与测试范围补充

首次最终 attempt `final-1637ba6b-8e86-49a1-8539-654692b32806` 绑定旧候选 `bd24f1b449e70a52cafbacb0a841b355870dadc8`：696 项测试，5 failures、1 error、1 allowed skip。四个 pipe 用例共享遗漏的旧 runner v4 正向 fixture，另两项文案断言仍要求旧的先全量后审查顺序/结果 footer。仅在 `tests/runtime/test_foreground_host.py` 与 `tests/runtime/test_repository_validation.py` 修复，保留旧版本拒绝、角色和安全断言；六项红绿和 45 项受影响检查通过。旧 attempt 原始失败和旧候选两项宿主报告保留，不覆盖、不复用为新候选通过。

Controller 批准必要测试连带补充：精确 implementation_paths 从 109 增至 110，仅增加 `tests/runtime/test_repository_validation.py`，无产品范围扩张。补充文件 `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/review-before-full-validation/stage3-test-scope-addendum.json`；SHA-256 `a2568cc70c36a9b2d3c54a1153e2757fa7f1bb3072e3297a9d145021e28d384f`。上方方案路径保留原清单，此处记录授权增量；冻结需求字节和模式保持不变。

### 最终验收事实

修复后的 I 重新通过两轴审查，再新建完整 attempt `final-ec656333-6b4c-4c76-991f-b1f636865283` 重跑所有必需项，未拼接不同 attempt 的通过项。

| 必需项 | 实际结果 |
| --- | --- |
| `./scripts/validate.sh` | exit 0；696 tests，0 failures，0 errors，1 skipped；构建一致性、Skill 和仓库检查通过 |
| 候选包真实单层 lifecycle | 新隔离环境和 raw events/report，passed，local exit 0 |
| 候选包真实单层 stop/pause/resume/cancel | 新隔离环境和 raw events/report，passed，local exit 0 |

唯一预批准 skip 为 `NonGitAttachedTransferTests.test_successor_dispatch_uses_real_ledger_slot_without_promoting_old_carrier`，原因 `wrapper transfer uses a Git stage-entry checkpoint`。整个必需检查均未 skipped。前后 HEAD 均为 I，源码字节和模式快照一致、工作区干净。候选包 digest `f8a732cc28afc740c183a23e26afe73acdf5ce7774647e251fbed2610ed0afde`；全量输出 digest `f98279cc5867b827d5dc0515f9116ba8587927616ddf269fba207ffce82db8b6`。

Evidence root：`/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/review-before-full-validation/`。准确 final attempt 子目录保留原始输出、宿主 readback、各项摘要和源码前后快照。Controller 已验证并接受 `final-validation-delivery.json`；digest、路径和模型声明本身不认证执行。归档检查文档、仓库及 Git 范围，不重复已经通过的长时 runtime 和宿主验收。

真实嵌套集成仍为 `pending-external-dependency`，未计通过；确定性生产链替身不证明真实嵌套能力。不声称来源、权限或丢失原生身份能够复活。本次无安装、部署、推送、PR 或外部写入，旧 EPERM 根因调查仍在范围外。
