# 06 — 必要兼容与最终交付验证（收缩修订）

Status: paused
Design status: accepted with scope amendment — 用户明确要求收缩，未恢复实现
Plan revision: 2026-10-07
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

## 执行步骤

| 步骤 | 范围 | 完成条件 |
| --- | --- | --- |
| S01 | 已确认原始 fixtures，以及真实失败定位到的已授权核心消费者 | 原断言可到达，权限/legacy/unknown 拒绝保留；只改实际依赖 |
| S02 | 现有协议文档/Stage 模板/静态校验、build/skill-packages.json 与五包输出 | canonical 生成且版本/闭包一致，不手改生成副本 |
| S03 | 既有数据限制报告、完整验证与两轴交付审查 | 最终候选原证据完整，本轮通过/延期/未验证分别列明 |

不新增现场 worksheet、计量脚本功能、对照样本或评估调度；已有 `scripts/measure_review_state.py` 保留 T03 成果，仅只读使用实际支持的输出。无 source 变化不重复构建；修复后只追加受影响检查，最终完整验证仍必做。

## 已确认的夹具范围

- `tests/runtime/test_stage_transfer.py`：新运行 input_for(3) 的 v1 内嵌计划改为合法 v2 catalog；保留旧运行拒绝。
- `tests/runtime/test_workflow_progress.py`：Layered/T04 的原公共 `ProgressTests.allocation_slot` 定义于此（53–60），适配原已提交归档的精确取数；不新增包装副本。
- `tests/runtime/test_layered_review.py`、`test_plan_revision_progress.py`：原停止角色 census 读取精确归档事实；CLI issuer 反例使用当前 C，仍验证 forged issuer 拒绝，并保留 stale C 拒绝。
- `tests/runtime/test_stage_scope_progress.py`：two_revisions 的旧 v1 planning producer 适配当前 v2；保留业务修订和原权限断言。

原失败日志：`t03-21-allocation-affected.log`（修改前核原文件名）、`t05-a-affected-layered-certificate.log`、`t05-a-affected-t04-claims.log`、`t05-a-affected-original-wrappers-attempt1.log`、`t05-a-affected-layered-cli-attempt3.log`、`t05-a-affected-t04-two-revisions.log`。其他 manifest verify 项保持只读，只有真实依赖证明且不扩大行为合同才按原范围晋升；新增机制须另确认。

## 验证与限制

V01：所有仓库测试只经 `scripts/run_tests.py`，最终 `scripts/validate.sh`。若 runner 报已有运行，报告 owner 并结束该次验证，不绕锁。

V02：`python3 scripts/build_skills.py --check`、静态校验、五包闭包及原版本拒绝。源和生成输出在同一候选一致；当前规划不生成、不安装、不部署、不远程发布。

V03：交付 Standards/Spec 两个独立轴只检查未覆盖增量、共享状态、需求覆盖与风险；Ticket 已有效局部证据不重复逐行审。发现实际变化或缺口才补对应复查。

完整原生宿主两Ticket/四片实验、私有修订/unknown/归档恢复现场采样、现安装入口/最终包 admission 验证、正式旧 v19 10片/3Ticket与新规则对照均延期。历史 preflight 仅证明当时模型/注册可用，不能冒作本候选现场通过；上线/安装前仍需另行确认并完成相应现场验证。现有命令日志不提供端到端墙钟或模型首次提交率，测试重试不替代模型样本；不据此推断成功率改善。
