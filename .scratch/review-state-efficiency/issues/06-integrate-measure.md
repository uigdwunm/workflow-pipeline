# 06 — 必要兼容与最终交付验证（收缩修订）

Status: in-progress — 用户 2026-10-08 明确恢复
Design status: accepted with scope amendment — 收缩边界保留；按 G1–G4 恢复执行
Plan revision: 2026-10-08（原收缩修订 2026-10-07）
Blocked by: 03, 04, 05
Spec: ../PRD.md
Scope inventory: ../manifest.json（T06）
Execution rules: ../execution-policy.md

## 当前交付与验收

只完成核心候选的已有支持路线兼容、原始测试夹具适配、协议文档和五包生成一致性、最终完整验证及交付两轴审查。保留 Matt 1.3.1/main `70822bc` 与 `GLOSSARY.md` 适配，不改变 Stage2 方案审查或新增入口。

以下替代旧现场/正式测量合同的本轮责任；旧方案保存在 Git `35d5a57876badce5a4beb01c99ac2a5db62c404e`，延期项不算通过。

- [ ] 在隔离 B 的核心候选修复已确认的原始夹具；保留原业务/拒绝断言，不靠包装副本长期绕过原测试，也不机械修改所有 verify 测试。
- [ ] 已有 standalone/attached、continuous/stepwise 与原宿主 adapter 路线的权限/读取/审查/停止恢复组合回归通过；延期初始化及 foreground ingress guard 继续拒绝。fixture 只证明相应接线。
- [ ] 五包生成资源与 canonical source 一致，独立资源闭包和 1.4.0 / progress-v20 / transfer-v15 / control12/schema7 / runner11 / execution-plan-v2 版本组一致；若实际占用才整体选择未用版本。旧运行误用新包明确拒绝，不迁移原包。
- [ ] 协议/Stage3/Stage4 文本、静态检查和最终 coverage 一致；完整仓库验证绑定最终候选，两轴交付审查核全部本轮 AC、跨 Ticket 风险和延期边界，复用未失效证据。
- [ ] 据既有原日志报告可核命令时间/读取/体积事实和 unknown；效率结论“证据不足”、完整原生现场“未验证”单列。不宣称原完整需求全部完成或宿主上线就绪。

## 剩余工作的分组与入口（2026-10-08）

当前核心候选为 `d2c15eb56bef923e0f15afa01171430dd5e06177`。已完成夹具/核心接线、canonical 包和文档修复及对应审查；最终完整验证中断，59/60 模块完成、29 PASS/30 FAIL，不能据局部审查宣称 T06 完成。本次只更新三个方案文件及唯一管理入口，不恢复源码修改或测试。

原始失败入口：[暂停验证清单](/Users/zhaolaiyuan/.codex/workflow-runs/01a10be8-b477-7401-8e1d-4e5079a1184d/implementation/t06-full/paused-full-validation.md)、[完整日志](/Users/zhaolaiyuan/.codex/workflow-runs/01a10be8-b477-7401-8e1d-4e5079a1184d/implementation/t06-full/01-full-validation.log)、同目录 `paused-worker-spool` 原模块日志与报告。旧验证仅绑定 d2c15eb；后续方案提交不重标其来源。

以下是剩余 T06 的诊断分组，不新增 Ticket，也不是已确认根因或文件写入授权。同一用例可能跨组；按实际产生者—消费者合同归属一次，保留必要组合检查。

| 组 | 待确认的行为边界 | 原失败定位入口 | 完成条件 |
| --- | --- | --- | --- |
| G1 计划输入与共享夹具 | 合法 v2 catalog、slices、digest 及所有实际消费者；保留明确的 v1/旧包拒绝 | 完整日志 2258：test_execution_plans；3499：test_workflow_control_git；867：test_plan_driven_dispatch；306：test_plan_revision_progress | 合法基线先通过；原目标断言可到达；旧版本反例仍拒绝；共享 producer 的直接消费者通过 |
| G2 当前/归档读取与原 CAS 写入 | 有限 current 与精确原归档取数；原 owner/CAS 保存；旧 full-inline 夹具的实际依赖 | 完整日志 9：test_workflow_progress；1652：test_review_first_validation；4132：test_pending_host_actions | 当前/归档读写闭环通过；旧 lease、stale C、无原提交证明及无写入反例保留 |
| G3 审查、前置受影响检查与停止接线 | local→Ticket→delivery 的原证据、候选边界、角色 census 和停止/恢复 | 完整日志 4937：test_layered_review；706：test_collaboration_lifecycle；1441：test_publication_progress | 原正常链及必要拒绝组合通过；角色/停止事实来自实际权威；不靠填假 pass 达成可审查状态 |
| G4 容量、读取预算与恢复核心缺陷 | 既有逻辑容量耗尽、subject 大小、普通读取预算和恢复结果 | 完整日志 9/4937 及对应 worker traceback：容量耗尽、subject 超限、读取预算超限；恢复先从 706/3434 定位 | 每个疑点有独立最小复现或其他可靠因果证据；区分无效夹具与实际核心缺陷；原容量上限、权限和原调用恢复保持完整 |

未归类失败暂记 unknown，逐条回到原 test ID/traceback 确认；模块名或相同异常文本只用于定位，不能直接判定为夹具问题。尚未完成的 `test_bounded_progress.py` 和最终静态步骤仍是完整 gate 的责任，不能遗漏。

每组开写前在本节维护一个有限记录：`原 test ID/日志引用 → 确认原因或 unknown → producer/consumer → 精确写路径及 manifest 权限 → 快反馈命令 → 受影响回归 → reviewer 结论/证据引用`。本文件是唯一修复索引；管理记录只留当前组、阻塞和引用，不复制全部 trace。原因未确定前不批量晋升 verify 路径，也不机械替换所有 v1 字符串。

优先定位 G1/G2 的共同入口并收敛合法基线，再处理其消费者；G4 可独立先诊断，不能预设为夹具。相互独立的只读诊断按需并行；同文件/共享入口只有一个写入者，紧密依赖合组或串行。同组固定实现者和独立 reviewer，findings 回原上下文修复，稳定后检查累计影响；每个补丁不新建任务或完整报告。

## 当前修复索引

G1：原失败日志 2258/3499/867/306/4343/504。已确认正输入 v2 与旧 v1 消费者不一致、函数变更未同步 slices，以及 unbound fixture 调用缺少实例 helper。精确写范围为 `test_execution_plans.py`、`test_workflow_control_git.py`、`test_plan_driven_dispatch.py`、`test_plan_revision_progress.py`、`test_stage_scope_transfer.py` 的原 setUp、`test_attached_flow_progress.py` 的 write_planning_spec（均在 tests/runtime）。manifest 已记录实际依赖；不改生产源码。首次快反馈：`python3 scripts/run_tests.py --test-file tests/runtime/test_execution_plans.py --match test_sealed_scope_exposes_versioned_execution_plan_envelope --jobs 1 --nice 10`。随后覆盖原 malformed arrays、scope transfer ceiling、standalone plan、真实 Git diff、multifile dispatch 和 inline revision B delivery；发现其他失败先核归属。实现者 `/root/t06_g1`，独立审查和原执行证据待补。

G1 字节已固定于 `218558e`（6 文件 73+/37-），作者停稳；24 项 execution_plans 与直接消费者已通过。命令/源指纹见外部 `t06-g1/summary.json`（运行时为 93dcc19+当时 WIP，不能改写为提交后来源），独立 reviewer `/root/t06_g1_reviewer` 审固定候选。两消费者仍待 G2/G3：13-inline-revision 缺原 Ticket acceptance；15-fake-accepted 用 finite header 读 control。此时不宣称 G1 全部组合通过。

G2/G3：原 3.log/9.log/25.log/59.log 加 G1 两条原失败，已确认 finite current 被当作 full owner、合法 runner11 初始化在支持初始 CAS 前提前 open_current、replacement census 构造先于 D2 行，以及候选缺真实 local/Ticket 验收。精确写范围为 `src/shared/scripts/workflow_progress.py` 两处初始化接线及其五包 canonical 输出，tests/runtime 下 pending_host_actions 的 unresolved、review_first_validation 原准备/恢复/读取/故障/legacy helper、layered_review 的 recover_layered_dispatcher、plan_driven_dispatch 的 fake accepted 单用例、plan_revision_progress 的 inline_revision_b_delivery 验收接线。manifest 已记录真实依赖；Store legacy guard、预算和原拒绝断言保持完整。首次快反馈为 pending 的 stop_does_not_settle_preceding_continue，然后 focused_candidate_can_review_but_cannot_complete、real_runner_stepwise_same_three_stage_chain、recovered_dispatcher_consumes_original_layered_claims；合法基线收敛后补 Pending/ReviewFirst 原模块及必要 CAS/legacy/组合。实现者 `/root/t06_g2`，独立结论及命令引用待补；源/runner 单写者串行。

G4：原容量耗尽用例已在 e503b46 经项目 runner 单例复现（3.2 秒、exit1），见 implementation 外部 `t06-g4/01-capacity-red.receipt.json` / `.log`。无新业务 dispatch；错误发生在原 archive intent 保存阶段。容量预检/错误返回路径继续诊断，保留 checkpoint 不变、无派发及固定预算断言，尚未修改源码。

G1 审查 P2：attached 原私有修订消费者残留 v1 regex，原作者仅改该一字符并固定 `4368bcc`。`t06-g1/21-attached-revision-red` 与 `22-attached-revision-fixed` 保留原时间/源码；后者已越过 ledger/CAS、激活/replay、scope/执行断言，但在 candidate-ready 缺 Ticket acceptance，整用例未通过。原 reviewer 只复查 P2 增量。G2/G3 精确追加同文件 `test_attached_plan_revision_uses_original_ledger_record` 的真实 local/Ticket/early-check 接线，manifest 已登记；不扩展 attached 初始化或接受假 pass。

G2 字节固定于 `044b83e`（17 路径，runtime 初始化仅两行接线、其五包 canonical 和原测试适配）。作者/runner 停稳，命令与 WIP 指纹保留外部 `t06-g2/command-index.md`、`final-snapshot.json`、`final-patch.diff`。两个真实 runner 模式通过；Pending 为原 14 项通过加实际 pre-CAS 原调用保留单例通过；ReviewFirst 原模块 28 PASS/11 红，后续定向复查覆盖全部 11 红，不重标原模块结果。fake-ref、foreign history、carried/adoption、未知 verdict、exact receipt ACK、inline 原 launch/lease/N5 真实性均有原公共调用证据。局部 reviewer 复查原候选，最终全量仍未执行。

G2 留三个入口：27 Layered 原 finalization_owner 缺 exact ref/cause；62/63 attached 真 Ticket-check 被原 adapter blocked/invalid_request，cause 未知；64 closed-docs 的共享局部审查 helper 硬编码 task，真实 Originating Task 身份被拒绝。前两项交 G4 真实原因探针，不削弱原停止/权限 guard。

G1 后续兼容小组：原 `test_workflow_control.py` 的 call/schema、v2 catalog/slices；`test_stage_scope_runner.py` 三个 current11/render 断言；`test_lifecycle_snapshot_retry.py` 通过真实 `_new_state` 与公开 C/owner 初始化构造四项原 journal/锁用例；`test_workflow_progress.py` 的 complete_fixture_local_reviews 从原 handoff 与 Controller 取身份，修 64 真实消费者。精确依赖已登记 manifest；scope_seal_control 保持只读消费验证。快速顺序为 pure preparation、accepted-witness、scope-seal、真实 Git changed-requirement、runner9项、合法 lifecycle 单例后4项、closed-docs 原消费者；未知 coverage 别构造假 pass。实现者复用 `/root/t06_g1`，source/runner 单写者；原 runtime/旧运行拒绝不变。

兼容续组字节固定 `c1601a5`（仅四测试文件 67+/42-），独立 Standards/localSpec 新 findings 0。pure schema/catalog/seal/Git、runner9项与真实 lifecycle4项通过；意外导入 TestCase 扩大发现库存的尝试已正常中断130，模块 alias 修复后原四项通过。公共 actor helper 不改变 runtime guard；closed-docs 已越过原身份门禁但实际 local/Ticket accept progress_failed 原因未知。pure-control 原23项为21 PASS/2FAIL，缺真实 coverage 的两项不冒作通过。原来源见 `t06-g1-compat/summary.json` 和 `independent-review-c1601a5.md`；G2 的早期28项完整字节适用性仍未证明，最后全量必须重新绑定。

G4 开工边界：先连贯修原 archive ordinary intent 预检和 Store 合法 raw/metadata 计账，固定2.25MiB/16MiB/18.25MiB与hash/原调用/CAS，不改物理B。精确源码为 workflow_progress.archive_action 与 workflow_state_store 原计账/三raw读取路径；五包 canonical 映射不新增。小 Store 回归覆盖合法 raw 后普通 predecessor、跨 owner/dependency 普通总预算；原大报告8份8MiB仍必过。精确测试范围为 state_archive 的两小回归、workflow_progress 原容量/恢复、layered_review 原大报告/替换角色、plan_revision_progress 原 ticket-comments subject。Subject serialization 后字段尺寸、scope 真实 Git 差集、native 原角色 accepted/stop 来源、attached adapter/closed-docs 原错误 cause 均先捕一次真实公开探针，未知不先改语义或晋升新源码。临时诊断在冻结前移除，仅保有限事实引用；本组作者 `/root/t06_g4` 独占 writer/runner。

G4 原小预算回归、容量拒绝/四 archive 邻居、audit/hash/cross-run 已通过；原8份8MiB报告71.92s通过，实测 metadata 1,120,008B、raw 2,097,152B、combined 2,383,333B，固定上限不变。原 evidence 02–15 保留各自 HEAD/WIP 来源，不重标最终候选。

G4 原因探针17/21确认 scripted-carrier actor 与 Controller 不同：observe/prepare receipt writer 用 Controller namespace，既有 reader 用原 originating actor；native anchor 又把原 actor 强制与 Controller 相等。精确追加 `workflow_control_git.verify_review_native_anchor` 与现有五输出，Progress 的 observe/preflight/commit_review_start/read_review_start_result 连贯匹配原 CAS prepare/observe 和原已验证 handoff actor，不信客户端自述，不动 generic Controller receipts、角色/来源/hash/停止 guard。18真实 integration diff 错用旧 baseline：只补原已验证 integration_target_head 为 expected_target_head 缺失时的基准，不加排除规则。22查明旧 native:dispatcher 有 activated recovery 停止事实却无原 business accepted owner；不得伪标旧业务接受，恢复结束权限语义仍 pending。Subject 与 attached cause 待实际探针。

G4 后续实际原因：24 closed-docs 原公开全恢复85.61s通过，原actor/CAS/anchor/closure均到达。22确认 native_refs 是注册/停止事实集合，native_owner_refs 合同只要求其子集；freeze consumer 仅对原有效 activated recovery 明确退役的已停止原 dispatcher 省略 accepted-owner，保留注册事实和旧角色 finalize 拒绝，其他缺 owner 照拒绝。26已走原D2交付/Stage4 transfer与四腐坏拒绝；额外 post-transfer 断言属于另一 adapter 身份先拒绝，已移除，最终正式 PASS 待补。

27实际 subject 274,367B，重复 dispatch.acceptance 48,768B；stage_subject_value/read_active_owner 仅复用既有 accepted body-ref，恢复后核原 sealed digest，预计体积225,821B待公开验证。28真实 ledger cause 为 Ticket check snapshot scope differs：`discussion_core/workflow_control.py:GIT_BACKED_ACTIONS` 缺 ticket-check/local-review/ticket-accept/review-applicability，原已激活 ledger 绕过既有 Git snapshot/native/CAS消费者。精确追加此源和现有五生成映射，只接回原 verified_transition；不新增动作/支持路线/ledger初始化。修复后原ledger原流程与真实拒绝邻居仍必验。全部临时 observer 已退出源码，原未知不重标通过；最终 canonical/check/独立局部审查/全量 gate 仍必做。

## 验证顺序与交付

G4 恢复链的已确认增量：原命令 32 暴露只读 Git 计划投影被当作完整可写 C 记录；37 证明 Stage3 没有自动归档定位，归档读取假设不成立。只在现有 `implementation_delivery` 的 `recovery_ref` 保存恢复必需的原真实计划记录、来源及 carried/revalidation 事实，由 `implementation_recovery` 核验原 CAS 引用、身份、权限和当前 schema 后恢复。保留普通 Git 投影、固定读取预算及无修订恢复路线；不扫描历史或构造缺失记录。原 31 attached ledger、33 actor/prepare/closure、35 D2/native owner 回归已通过，完整恢复及最终全量仍未完成。

1. 每组先选择通过项目 runner 的最小反馈命令，证明合法输入和目标失败确实可达；准备失败先修合法基线，避免反复跑长模块寻找同一输入错误。
2. 修复后跑实际失败用例和共同入口的直接消费者，再跑必要的真实 C/Git/CAS/停止与审查组合。纯格式反例只构造对应公开边界需要的合法输入；权限与接线仍用真实模块验证。
3. 公共输入/读取入口稳定、已知失败已处理后固定候选，完成 `scripts/validate.sh` 及最终 coverage/两轴审查。完整 gate 失败则按实际影响回到对应组；修复后重新绑定最终候选完成全量，不跳过失败模块，也不以局部通过替代。

每组只做一次派发预检：确认上下游、写范围、快反馈、必要回归和审查覆盖。沿用现有 runner 的最多两个 worker 和 nice 10；不新增 failfast、调度器或性能平台。依赖变更才重做对应 canonical 生成和审查，未失效证据继续复用。

从下一条实际命令开始前置绑定 argv/选择条件、候选 HEAD 与相关输入来源、起止、退出码和原输出引用；在冻结/交接点记录必要指纹。旧日志缺失字段保持 unknown，不事后补写成当时证据。原完整验证命令 real 5730.51 秒说明反馈周期较长；模块并行耗时不能相加冒作端到端墙钟，当前不承诺提速比例或模型成功率提升。

不新增现场 worksheet、计量脚本功能、对照样本或评估调度；已有 `scripts/measure_review_state.py` 保留 T03 成果，仅只读使用实际支持的输出。无 source 变化不重复构建；最终完整验证仍必做。

## 已完成适配的历史范围与证据入口

- `tests/runtime/test_stage_transfer.py`：新运行 input_for(3) 的 v1 内嵌计划改为合法 v2 catalog；保留旧运行拒绝。
- `tests/runtime/test_workflow_progress.py`：Layered/T04 的原公共 `ProgressTests.allocation_slot` 定义于此（53–60），适配原已提交归档的精确取数；不新增包装副本。
- `tests/runtime/test_layered_review.py`、`test_plan_revision_progress.py`：原停止角色 census 读取精确归档事实；CLI issuer 反例使用当前 C，仍验证 forged issuer 拒绝，并保留 stale C 拒绝。
- `tests/runtime/test_stage_scope_progress.py`：two_revisions 的旧 v1 planning producer 适配当前 v2；保留业务修订和原权限断言。

这些已完成适配不自动覆盖新失败；实际目标适用性须核验。原失败日志：`t03-21-allocation-affected.log`（修改前核原文件名）、`t05-a-affected-layered-certificate.log`、`t05-a-affected-t04-claims.log`、`t05-a-affected-original-wrappers-attempt1.log`、`t05-a-affected-layered-cli-attempt3.log`、`t05-a-affected-t04-two-revisions.log`。其他 manifest verify 项保持只读，只有真实依赖证明且不扩大行为合同才按原范围晋升；新增机制须另确认。

## 验证与限制

V01：所有仓库测试只经 `scripts/run_tests.py`，最终 `scripts/validate.sh`。若 runner 报已有运行，报告 owner 并结束该次验证，不绕锁。

V02：`python3 scripts/build_skills.py --check`、静态校验、五包闭包及原版本拒绝。源和生成输出在同一候选一致；当前规划不生成、不安装、不部署、不远程发布。

V03：交付 Standards/Spec 两个独立轴只检查未覆盖增量、共享状态、需求覆盖与风险；Ticket 已有效局部证据不重复逐行审。发现实际变化或缺口才补对应复查。

完整原生宿主两Ticket/四片实验、私有修订/unknown/归档恢复现场采样、现安装入口/最终包 admission 验证、正式旧 v19 10片/3Ticket与新规则对照均延期。历史 preflight 仅证明当时模型/注册可用，不能冒作本候选现场通过；上线/安装前仍需另行确认并完成相应现场验证。现有命令日志不提供端到端墙钟或模型首次提交率，测试重试不替代模型样本；不据此推断成功率改善。
