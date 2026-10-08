# 06 — 支持路线一致交付与可比质量效率测量

Status: ready-for-agent
Design status: accepted — 普通本地连续实现已授权
Plan revision: 2026-10-07 — 用户确认前置采集与工程/效果分别验收；实现保持暂停
Blocked by: 03, 04, 05
Spec: ../PRD.md
Scope inventory: ../manifest.json（T06）
Execution rules: ../execution-policy.md（计量来源、代表场景及效果结论）

## What to build

在当前真正支持的native路线完成两个Ticket的审查、局部修复、内部修订、恢复和最终交付；所有五包资源/协议一致，旧运行明确保留原包。汇总T03起采集的数据，使用可重现任务与真实宿主样例报告质量、耗时、活跃体积和完整恢复读取。工程验收和效果结论分别报告，不以测试通过冒充提效。

## 合同和验收

- [ ] standalone及已有attached控制/Phase Run语义、continuous/stepwise、app-server/collaboration读取/审查/停止恢复一致。
- [ ] 延期的无Ticket修订初始化、foreground Controller ingress、attached Stage2/Phase0初始化继续拒绝，测试不掩盖为成功。
- [ ] 新包1.4.0拟定版本组原子一致；旧progress/runner/control记录不改写且误用新包明确拒绝。
- [ ] 五包独立资源闭包包含store/review模块，生成资源与source一致，Stage4验证完整coverage和最终pass。
- [ ] repository完整验证通过，原生宿主现场样例另附原调用/停止/回执证据；fixture不能替代真实host。
- [ ] 汇总T03起的原始计量并覆盖独立文件、连续修改共享文件、暂停恢复三类场景；报告PRD全部指标、失败/中断和可比性限制，缺失数据为unknown。
- [ ] 权限、支持路线、故障行为及空间/完整恢复读取为硬门槛；效果单列改善/未改善/证据不足，未测得时间/质量改善不宣称提效完成，现场未做不宣称现场通过。

## Function Execution Plan（草案）

| Step | 目标符号/声明 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | `workflow.py.validate_confirmed/_validate_record/_verify_run_completion`、`stage_handoff.require_current_protocol/verify_result`、`discussion_core.workflow_control.workflow_control` | 已集成新protocol输入→同义接收/拒绝；保留延期guard及原版本拒绝 | 无 |
| S02 | `build/skill-packages.json`资源roots/compatibility、`scripts/build_skills.py.build/validate_files`（核验） | 新source→五包闭包与manifest；缺模块/旧protocol/跨包链接拒绝，generated不手改 | S01 |
| S03 | `scripts/validate_repository.py.validate_workflow_control_docs`及协议/Stage模板声明 | 一致审查/状态/恢复语义→静态合同检查，移除冲突旧句，不改变Stage2方案审查规则 | S01 |
| S04 | `scripts/measure_review_state.py.main`（T03新增并开始采集） | 原run/命令/审查事件与固定场景标签→JSON+Markdown对比；总墙钟/执行/重叠分列，缺失为unknown，拒绝混合protocol/model/environment当同组 | T03的计量来源（采集不等待S01） |
| S05 | `scripts/field_acceptance.py.main`（已核验现有入口） | 新真实宿主worksheet包含局部修复/短屏障/unknown/最终Stage4场景与各指标；旧延期路线单列，不自动部署 | S02、S03、S04 |

模块CLI测量需区分wall/CPU、协调/验证重叠，固定样本ID而非按成功挑选。外部模型差异单列，不采用不同模型结果宣称协议提升。发布仍需后续用户授权，不在本Ticket安装本Mac。

先汇总已有可比日志和小样本退化，再完成支持路线/两Ticket组合及一次当前候选完整验证。现场证据、工程硬门槛、统计效果各自有结论；原10片/3Ticket对照目标及少于30片的限制按PRD保留。小样本足以发现明显退化，不能证明成功率提高；正式数据不足明示证据不足，不重跑旧任务来伪造完整样本。

## 审查归属

| 验收项 | 首次检查层/证据 | 上层责任 |
| --- | --- | --- |
| 1、2、3、4 | 局部：V01/V02的支持/拒绝/版本/五包合同 | Ticket核各路线共享语义 |
| 5、6、7 | Ticket：V01最终完整验证、V03原生现场与S04真实计量 | 交付两轴核完整需求/跨Ticket风险及准确的结果声明，复用仍适用的局部证据 |

## Verification references

V01：manifest内列明的全部相关runtime/package测试经 `scripts/run_tests.py`，最终 `scripts/validate.sh`；若占锁报告owner并结束该次验证。

V02：`python3 scripts/build_skills.py --check`、静态repo核验与版本清单；外部生成包只在实施时产生，本次规划不生成。

V03：真实host按PRD两个Ticket/四片样例：一处局部bug、一处共享fixture变动、一处私有plan revision、一次未知原调用、一次归档后恢复；保存native IDs/停止证明/包digest，并验证Stage4接收当前候选。现场未执行就明确标记未验证。

效果场景显式覆盖同文件连续修改导致的保守失效与累计重审，不能只用互不读写的a.py/b.py证明收益。T03的来源/重叠/unknown反例适用时复用；S04核原数据与报告一致，必要时才晋升现有verify测试范围，不新建评估调度服务。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。


## Matt 1.3.1 基础兼容校准（2026-10-07）

先继承 main `70822bc` 的路径与外部依赖约定。本项目新增领域术语只写 `GLOSSARY.md`；消费项目既有 canonical `CONTEXT.md` 仍有效，阶段提示与文档索引传递选定路径。上游静态兼容基线为 `dd400c3ad65e57c06f05e832e0aac92c7992f34d` / `1.3.1`，动作名称和六 Ticket 合同不变。

V02/V03 必须区分 main 历史宿主 preflight 与本分支新包：核真实安装的宿主适配入口及 exact package identity，main 的十项 action preflight 不替代新 control12/schema7/v2 计划下的原生交接或完整端到端样例。仅更换词汇表路径无需重跑 T01/T02 全套，也不重标其原审查源点；此兼容增量的构建、静态和包回归单独记证。原完整 validate.sh 和未验证原生现场仍按本 Ticket 执行。

## Confirmed fixture consumers (2026-10-08)

T05-A original affected checks found current-only allocation lookup, current-only stopped-role census, stale C input in the CLI issuer counterexample, and a v1 stage-scope planning producer. T05 used exact archive-aware wrappers in its owned test module and preserved every original business/rejection assertion. T06 owns the corresponding original fixture adaptations in `test_layered_review.py`, `test_plan_revision_progress.py`, and `test_stage_scope_progress.py` before final full validation. Original logs: `t05-a-affected-layered-certificate.log`, `t05-a-affected-t04-claims.log`, `t05-a-affected-original-wrappers-attempt1.log`, `t05-a-affected-layered-cli-attempt3.log`, and `t05-a-affected-t04-two-revisions.log`. This is new-run fixture compatibility; original legacy refusals and frozen requirements remain.
