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

## 历史修复索引（保留原候选来源）

以下保留原候选时的结果与未知项，不作为当前派发指令；已关闭项不重跑。当前范围见“验证顺序与交付”，活动作者、runner 和下一步只由唯一管理记录维护。

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

最终原完整验证 `81e61f0` 已正常结束：60 模块 39 PASS／21 FAIL，1423 项、46 failures／67 errors／1 skip，exit 1，6845.043 秒；625 tracked 输入前后完全一致。失败不是验收，原日志和字节保持。按已保存的原失败共同产生者继续修复，不新增 Ticket 或延期功能，也不能用局部绿替代最终全量。

首个修复批次只涉及三条基础路径：原 `workflow_control_git.verify_stored_review_coverage` 的 committed Git 对象源选择；`workflow_progress.prepare_review_archive/native_refs` 先经一次旧 reviewer 原 action/archive/census/pending 探针确认的身份保留；原 `ProgressTests.invoke/observation/complete_fixture_local_reviews` 对有限身份、真实连接和 admitted adapter 的构造。保原保护范围、已注册角色、原停止／查询／CAS／来源证明、固定预算与实际拒绝。原作者唯一写入与 runner；其他失败测试先只读，后按已证明依赖登记必要适配。同源生成副本仅按原映射同步，完整候选必须重新通过 `validate.sh`。

### 完整验证失败后的必要修复批次

基础批次已冻结：原 Git committed 对象读取固定 repository；退役 reviewer 只以原 action→原 compact slot ref 保留登记，不恢复停止或验收权限；共享测试读取真实 finite transport 并按 admitted adapter 构造新审查者。18 路径冻结 snapshot SHA256 `6c3683a0645c307c5896e26edfffb1e940392adb726d32622e7fc12d782fb60c`、patch `857b1aa482a666e00c9d785792108ceaf6b8dba95810e14fd259a1e285381645`，原命令索引 `t06-final-foundation/command-index.json`（12,452B）。completion、实际 adapter、退役 stop/source 拒绝和最大八份8MiB报告通过；原 full02 仍失败，独立增量待审。

ordinary／raw／physical 独立峰值为 1,110,259／2,097,152／2,393,053B，各自低于原固定限额；峰值不能相加。新登记沿同 operation 聚合普通读预算，Stage Subject 测写在物理落盘之前；没有新条数限额、读取豁免或旧运行迁移。

下一阶段一次登记已证明的原测试依赖，按共同边界串行，不按失败模块新建任务：

| 原作者 | 原测试路径（均在 tests/runtime） | 最小完成边界 |
| --- | --- | --- |
| G1 | plan_revision_progress、plan_driven_dispatch、dispatch_recovery、requirement_delivery、discussion_protocol、stage_transfer、stage_scope_transfer | 当前计划/schema7正输入、原local/Ticket前置、原精确allocation/归档proof消费者；保旧版本、witness、ownership和planning越权拒绝 |
| G2 | attached_flow_progress、publication_progress、stage_scope_progress、accepted_query_retry、business_recovery、entry_recovery_chain、unified_recovery、stage_start_verification | 原owner／transaction proof／CAS故障点；不重复Phase、不回写或回退header，完整业务断言与原调用恢复不变 |
| G4 | collaboration_lifecycle、foreground_host、design_recovery、bounded_progress | 原adapter／census／stop、原finite proof与real-pipe输入，真正subject溢出和新业务调用身份；不删除角色、fake stop、放宽deadline或预算 |

原只读原因与selectors引用 `t06-final-diagnosis` 的各共同入口报告，manifest `final_repair_fixture_scope` 绑定精确路径。基础命令11已越过审查者namespace，在collaboration第474行读取退出current的first-slice失败：只能通过原精确归档slot/proof取数，不重标该用例PASS。每组先落实已确认的最小修改再跑原失败短反馈；下游未知按实际返回值定位，新增生产路径或公共合同另行诊断。共用入口仍唯一writer/runner，原reviewer可只读固定Git增量，整合后的完整gate必重跑。

七测试批次已经冻结：205+/104−，patch SHA256 `973dd7294e865b65385d3376283beef3d8a09f0ae13f6354dc4a768a9ea5bb23`，snapshot `76e7b407aa726ab7636d72cb980b45f37dd28d5b0c51f8d75c0e7b7a3ab2173f`；七路径原test IDs和13依赖字节未变。索引 `t06-final-plan-fixtures/index.json` 为6,158B，四命令分页均小于16KiB；42次实际命令20PASS/22RED不是模型样本，旧输出不重标。真实local/Ticket/directGit、partial/carry/witness、current7/legacy5、独立slice、T04原引用故障/七allocation、zero-delta和StageTransfer保护边界有定向PASS。未跑完整46项或Recovery13，不能签该组/最终全量PASS。

下一有界运行时批次复用原G4，先取得实际最小源反馈，再连贯修复以下缺口：

- `dd11e5c`独立审查Standards0/localSpec P2 OPEN：旧globals已精确归档并注册，首次交付owner freeze却只消费current claim/活跃history/allocation archive，缺旧review archive的原actor/receipt/parent业务来源。复用现有CAS验证的read_native_archive恢复历史finalization语义，保freshstop/adverse，不授当前候选或新lease；新增原真实repair→不同新globals→first delivery/Stage4组合先RED。
- 原命令02：已有原C intent的late bind被pending new-preparation readiness拒绝。只在stage_dispatch.record_for核真实原intent/receipt及current scope后区分原调用settlement；newprepare门禁和attempt/ref/digest/matching/ended/unknown/peerstop拒绝不变，不改通用read_scope_readiness支持范围。
- 原命令31：真实dispatcher result receive→C.save的stage subject264,520B超过固定262,144B，丢失accepted response目标未触发；失败时checkpoint是否不变未采集，保持unknown。先捕一次真实字段尺寸/原对象来源，只有已证明冗余projection或必要引用producer才修改，不增cap、不删rejected audit/stop/history/claims。
- 原19–21：许可intent已有，但合法original-host identity/resumability producer未证明；只沿现有支持的真实fixture/admission与原saved动作核边界，不新增foreground路线或任意补身份。原43只剩readonly source_digest零值反例不拒，后续公共revalidate、第二accepted和new digest断言到达；该projection不是发布C，不能据此宣称权限漏洞。先核verify_inline_recovery_pin原已捕获源的对应关系，必要比较须绑定既有原proof；保原四字段和真实source拒绝目标，不扩expected集合或放宽authority。

本批精确路径及条件见manifest `final_runtime_repair_scope`：workflow_progress、stage_dispatch、stage_handoff的上述原seams及三个必要原测试消费者。stage_handoff目前只新增原source关系诊断，不据纯counterfactual假定产品风险；不触及Store/runner或额外支持。原五包映射只同步实际变化源，所有原证据和固定预算保持。只读review可检查已冻结七测试Git对象，另一源码writer不得并行。该批及剩余owner/native fixtures、完整validate与最终交付仍未完成。

上述运行时批次已冻结：26路径精确SHA/patch/modes核对，三原脚本及15 canonical、5包digest、三原测试；201个原test methods保留，仅新增一个真实旧globals首次delivery组合。source snapshot SHA256 `59ebb018de7f2978f99f633f4d5235828cce6900c5517565e1a12dc55585cf63`，patch `31deae1a3e44f472c90e80234e93b13cbd3e43af53c360424f66a92682e80383`，原29条命令由 `t06-final-runtime/index.json`（2,805B）及两命令页（11,492/11,246B）定位，不重标原RED。累计独立增量待审，原完整full02仍FAIL。

实证：23原旧globals→pending record/source读与missing/corrupt/foreign/adverse拒绝、accepted B结果已存未consume恢复→scalar转换→first delivery/Stage4 PASS29.28s；22原late binding/no write_release/activepeer及pending新prepare/伪造intent拒绝PASS20.98s；15真实saved idle→continue/running保原pending→完成group后原call settle→local/Ticket/final/Stage4 PASS63.63s。原21缺证明只读诊断已由实际causal gate和原支持的idle producer闭合，不新增foreground机制或伪造resumable/transport。

实际09 capacity探针复现receive subject264520B且仅该09采到checkpoint前后同SHA，旧31未采仍unknown。同一16462B原observation在transaction_source重复，只复用既有observation引用。随后accept intent新原16/17边界279420B：dispatch与transaction.record同16818B收到B record，last_result.record为同received record来源；仅这两份复用同ordinary evidence引用，dispatch正文保持。原intent/result未consume与consume后scalar落点均由真实公开23验证，源缺失/腐坏拒绝不写checkpoint；无新type/schema/cap、无审计/stop/claims删除。20原丢accepted响应故障确在accepted之后触发，同completion恢复、rejected-ID审计、四illegal history最早save/inspect拒绝与published条件CAS补偿、真实Stage4 PASS115.72s，原G1测试P2作者修复建议已整合，独立闭合尚待。

21原inline完整PASS44.62s：仅原nonempty adopt_paths比较原capture snapshot与已有semantic adoption hash；实际公共wrong semantic和checkpoint不变、四readonly pin/source、foreign/nointent/protected/revalidate/第二accepted/newdigest保留。只读counterfactual不冒作publishedC漏洞。24原fresh stop/old ACK不清撤销19.65s、25暂停处理原allocation5.98s、26ended/notcreated不被late ready复活3.54s通过。27最大八份8MiB报告PASS64.04s，独立ordinary/raw/physical峰值1116706/2097152/2393053B分别低于原限额；Root2990B和该样本Subject44160B，非所有历史规模保证，峰值不相加。28canonical5.09s、29diff0.081s通过，observer已清。

当前继续原已登记G2八owner/CAS测试、G4四native consumer夹具，源只读增量审查用固定Git，与下一独立测试writer可并行；不添加新Ticket或延期功能。所有必需夹具/累计findings闭合后冻结最终candidate，完整validate仍必跑。本段报告实际来源结果，不签whole T06/native/efficiency。

G2 owner夹具批次已冻结，八原测试路径、原AST库存、文件模式、归档source副本与49,457B patch精确核对；freeze SHA256 `37486972cadb041ad1f35a8e3a51d69e815ae3dabcd3ca5bd6a3dc7cf9f9e8e7`，patch `e13f05f2a45cbceb8d308bf4fe910f9ad193fd6af3800689df144f3dfcbbdb35`。命令索引 `t06-final-owner-fixtures/command-index.md`（10,226B）保存原输入/时间/exit/log，不重标旧失败；作者最终关联49个原tests定向PASS，不是完整八module或最终validate。生产/共用helper/包字节未变，局部独立增量尚待。

原typed saved result与exact owner/CAS、query retry singleCAS、Unified真实callback故障、hostloss/cancel、scope seal/stale/legacy以及全部business字段的lifecycle重放已到达。Publication17原失败ID逐项闭环；cancel/pause-cancel/recover各分支改为独立真实C，不复活或回滚已消费权威。Stage4四暂停原casePASS448.02s（必要真实分支设置成本），unsaved2为78.39s、intake1为111.76s；无同字节重跑完整module。原request/decision/unknown/no B或宿主重发/Git/source/proof不变断言均保留。

原attached worker input从当前已激活Stage3 handoff/Phase/run/attempt/source取得，仍由实际Topic actor与expected_entry真实双身份验证，不创建第二Phase。allocation snapshot核实际prepare时独立Git HEAD、原baseline ancestry及全部旧fingerprints；current/archived执行结果仍按exact taskID/allocation digest/refs。原publication/cleanup/Phase replay全目标PASS226.18s；final recovery需真正currentTopic/native caller的unchanged integration adoption分配/接受/停止而不是readonly prepare，完整目标PASS376.80s，completed3/active4、readonly和separateDispatcher拒绝、原source/recovery均保。replace前fault hook从真实prepared root/subjects/ref/hash恢复原B record来计算request digest，不读latestC猜request或改故障时点/预算。42原recover新红由constructor已经提供replacement、旧test重复append同ID造成；只删重复append并核原完整唯一row，46原新/旧business block与proof/history恢复PASS19.36s，47 before-start gates和48 stale query通过。所有RED保其原source，不把owner fixture当native现场或正式效率证据。

当前转最后四原native消费者夹具（已有manifest范围，原G4唯一writer/runner），八test固定Git可由原reviewer只读并行；运行时5e81的累计审查Standards0/localSpec新0，两原P2已闭合。四夹具及其实际影响、最终完整gate和交付两轴仍必需，不宣称whole T06完成。

下列段落保留先前候选的检查时点；其中“待补”“下一位”属于该时点，不覆盖本批范围或唯一管理记录。

G4 固定 `91c5a54` 原审查者已关闭恢复链 P2，Standards 0、localSpec 新发现 0；预留前写入修复的 `2bd9943` 增量结论保持。后续两测试文件的原 Git 29 项、pure 23 项和 peers 用例通过，原 test IDs 保留；冻结 patch SHA256 `0e4dd983dfa4434157f9a7cf22ce93021d0af53992c9a65e8fc513f014900db5`。真实初始包 pin、A/规划基线、bound dispatcher 接收及原 Task Ticket 检查、原 predecessor/保护路径均经已有产生者接线，生产代码和包未再改变。当前待该测试增量独立审查、固定候选完整验证和最终两轴交付覆盖，不能标记 T06 或原生现场完成。

G4 原 69 两次真实恢复、11 类拒绝和旧角色无验收权限断言通过；70 原单次、71 最大报告、72 生成一致性及 73 diff 检查通过，冻结增量待原 reviewer 关闭 P2。下一位唯一作者只修 `test_workflow_control.py` 与 `test_workflow_control_git.py` 的已确认 Ticket/coverage 前置：纯 control 数据结构夹具明确只证明 schema，真实 Git 消费者使用原公开临时 C 分配/接受/局部与 Ticket 证明及原 closure delivery 引用；保留原正反目标和监督清理，不改变生产 guard 或支持路线。G4 停稳后交接，审查读取固定 Git 对象与 G1 实现并行。

G4 原 56 公开 prepare/容量拒绝及 57 两条 ledger CAS 真正目标通过，原 51 RED 保留。固定 `1ddbdbe` 早审 Standards 0、localSpec P2 OPEN：原直接替换者判断不支持已有同配置 `D1→D2→D3` 恢复链。只在原 `freeze_implementation_native_owners` 按有界原 `(ref, attempt)` 激活链、原旧 dispatch/ready receipt 和完整未撤销 stop/host 证明到当前真正验收角色；不授予旧角色业务验收权限。原 Layered helper/公开恢复用例补两次恢复及断链/循环/失效证明拒绝。该修复及独立增量复查、最终全量仍待完成。

G4 原 45 完整带修订恢复、48 发布竞态及原恢复对象缺失/坏哈希拒绝、49 最大八份 8MiB 报告通过；53 生成一致性通过，原输出保其命令/源码来源。原 51 两条 ledger CAS 用例仍未到目标：`review_activity` 在容量测量和预留之前物理写入 issuer/source 对象。只将这两个原对象纳入已有模拟测量和普通 intent 字节，通过容量校验及预留后再落盘相同引用；不改变原外部调用、身份、回执或 CAS 合同。此组及最终全量仍未验收。

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

### 最后四个测试消费者已冻结

以 `723cb94089de0eae5f123fb7eb36cce5962df35a` 为源基线，仅 collaboration_lifecycle、foreground_host、design_recovery、bounded_progress 四个原测试文件适配。原 70／31／8／137 个测试 ID、继承及 load_tests 不变；runtime、共享 helper、包和 runner 均未修改。真正使用的归档证明损坏拒绝、待关闭任务屏障、当前 real-pipe、前台三阶段 C/B/Git 和无重发、双任务停止屏障、第二轮独立业务恢复、真实 delivery 溢出无副作用均取得原目标定向 PASS；readonly attached-design 邻居通过。

源快照 `t06-final-native-fixtures/candidate-source.json` SHA256 `5ca9b0be72d95ce27ad3636898f436b7ff48e1e508ae37b6c1f1696cbb7014df`；patch SHA256 `62a2514c9a4cf76160de5b0f48d1535bc0bdc9c879956cce3b5ac88bab71484a`。43 条实际命令与四个小于16KiB的分页见 index.json；original-failed-targets.json 对应原失败 ID。混合失败命令23／26保持原 exit1，未失败子用例仅按原 SHA 与后续局部 delta 限定复用，不能标为整文件通过。临时探针已移除，原作者和自有 runner 已停稳。

运行时固定 `5e81a9f` 和 owner 固定 `723cb94` 的原独立审查均为 Standards0／localSpec新0；前者已关闭 dd11／ec 两项原 P2。下一步只有四测试固定增量审查、原交付两轴未覆盖增量与最终完整 gate；不重新审已有效 Ticket 证据、不新增机制。原 full02 的失败和 full01 的中断保持原候选与输出，最终候选须 clean 且全量运行前后输入相同。最终验证事实写入已有外部唯一管理记录与命令回执，不为结果再改动候选源文件。

### 本次短收尾，完整验证保持暂停

用户确认只执行短收尾并停在干净候选。原 full03 固定 `d4aeab3` 在用户要求暂停时正常中断，exit130、3074.047秒，625源输入前后相同；完成6模块5PASS/1FAIL。原计划修订模块46tests中的两FAIL保留原worker/log与只读诊断，不能改标全量通过。

唯一测试源改动为 `test_plan_revision_progress.py` 的1+/1−：初始五个v1真实 public accept 后、生成修订report前调用原 Ticket check，让reader已验收bytes在原authority下被原fingerprint/receipt验证并提交，再进入v2；未扩大accepted集合或删断言。仅移除直接T04调试print，保measured、容量断言及原metrics。原两final_integration用例以同一次 runner筛选完整通过，2tests／0failure／0error／0skip、557.015秒，后半实际恢复、最终快照与保护检查均到达。46个原test IDs、继承及load_tests不变，runtime/helper/包/runner未改。

`t06-short-plan-fix/index.json` 记录实际argv/head/15输入SHA/起止/exit/log；源码快照SHA256 `33e40ba6adc5922006fd112f072240fb023d5c66b594f2433b1a774e4aec332e`、patch `22b281a616075b524c2670b1178f0804b483d562bbbf946d6d070750473a9cad`，原作者与runner均已退出。接下来仅固定此增量交原reviewer核对并暂停，实际审查结论写已有外部唯一管理记录；不启动完整validate，不合入main，不安装或部署。最后全量在后续用户明确授权的连续窗口运行，候选期间保持不变；原full02失败/full03中断继续保留。

### 最终7a全量的唯一失败范围

原 full05 已正常结束：固定 `7a18595`、exit1、9023.613秒，625源输入前后相同；60模块59PASS/1FAIL，1424tests／5failure／1error／1skip。预算137项整模块通过。唯一失败是原dispatch_recovery的13项模块，原worker17.json/17.log与只读诊断保存于 `t06-final-diagnosis/full05-dispatch-recovery-7a`；不重标原失败。

接下来复用原G1，仅原test_dispatch_recovery.py修已证明的测试producer：原observation已经带ready身份，手工追加造成duplicate并被正确拒；改用原完整唯一census并确认原stop barrier成功。原transport必须通过finite outer CAS；原exact control intent重放应ACK且无effects，真正lookup仍用原公开推进；中断挂实际bounded activated commit。必须保原独立duplicate/source/oldwriter/unknown/index/content/deletion拒绝，原unaccepted allocation执行权释放和not-created后同intent重试目标不能删。后二者尚未有合法基线动态结果，不判断为runtime缺陷；生产脚本、共享helper及包保持只读，真实源因另按原边界登记。

原完整runner已退出、原锁无holder，才能交源码writer。共同producer修后直接跑原13项完整模块，避免只证明一两个入口而遗漏同helper分支；新原因先最短真实反馈。原reviewer只核冻结增量，其他有效证据不重审。最终新候选仍需完整validate，运行前完成必要规划并冻结，原全量事实继续由外部唯一管理与实际回执保存；不新增机制、放宽预算或删除失败模块。

## 验证与限制

V01：所有仓库测试只经 `scripts/run_tests.py`，最终 `scripts/validate.sh`。若 runner 报已有运行，报告 owner 并结束该次验证，不绕锁。

V02：`python3 scripts/build_skills.py --check`、静态校验、五包闭包及原版本拒绝。源和生成输出在同一候选一致；当前规划不生成、不安装、不部署、不远程发布。

V03：交付 Standards/Spec 两个独立轴只检查未覆盖增量、共享状态、需求覆盖与风险；Ticket 已有效局部证据不重复逐行审。发现实际变化或缺口才补对应复查。

完整原生宿主两Ticket/四片实验、私有修订/unknown/归档恢复现场采样、现安装入口/最终包 admission 验证、正式旧 v19 10片/3Ticket与新规则对照均延期。历史 preflight 仅证明当时模型/注册可用，不能冒作本候选现场通过；上线/安装前仍需另行确认并完成相应现场验证。现有命令日志不提供端到端墙钟或模型首次提交率，测试重试不替代模型样本；不据此推断成功率改善。
