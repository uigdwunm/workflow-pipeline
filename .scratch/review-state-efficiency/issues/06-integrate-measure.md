# 06 — 必要兼容与最终交付验证（收缩修订）

Status: paused
Design status: accepted with scope amendment — 收缩边界保留；本次只更新执行安排，未恢复实现
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

## 验证顺序与交付

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
