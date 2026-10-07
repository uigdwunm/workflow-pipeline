# 06 — 支持路线一致交付与可比质量效率测量

Status: ready-for-agent
Design status: accepted — 普通本地连续实现已授权
Blocked by: 03, 04, 05
Spec: ../PRD.md
Scope inventory: ../manifest.json（T06）

## What to build

在当前真正支持的native路线完成两个Ticket的审查、局部修复、内部修订、恢复和最终交付；所有五包资源/协议一致，旧运行明确保留原包。使用可重现任务与真实宿主样例报告质量、耗时、活跃体积和恢复读取量，不以全测试通过冒充效率改善。

## 合同和验收

- [ ] standalone及已有attached控制/Phase Run语义、continuous/stepwise、app-server/collaboration读取/审查/停止恢复一致。
- [ ] 延期的无Ticket修订初始化、foreground Controller ingress、attached Stage2/Phase0初始化继续拒绝，测试不掩盖为成功。
- [ ] 新包1.4.0拟定版本组原子一致；旧progress/runner/control记录不改写且误用新包明确拒绝。
- [ ] 五包独立资源闭包包含store/review模块，生成资源与source一致，Stage4验证完整coverage和最终pass。
- [ ] repository完整验证通过，原生宿主现场样例另附原调用/停止/回执证据；fixture不能替代真实host。
- [ ] 报告PRD所有指标及样本限制；空间/恢复预算为硬门槛，未测得时间/质量改善就不宣称提效完成。

## Function Execution Plan（草案）

| Step | 目标符号/声明 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | `workflow.py.validate_confirmed/_validate_record/_verify_run_completion`、`stage_handoff.require_current_protocol/verify_result`、`discussion_core.workflow_control.workflow_control` | 已集成新protocol输入→同义接收/拒绝；保留延期guard及原版本拒绝 | 无 |
| S02 | `build/skill-packages.json`资源roots/compatibility、`scripts/build_skills.py.build/validate_files`（核验） | 新source→五包闭包与manifest；缺模块/旧protocol/跨包链接拒绝，generated不手改 | S01 |
| S03 | `scripts/validate_repository.py.validate_workflow_control_docs`及协议/Stage模板声明 | 一致审查/状态/恢复语义→静态合同检查，移除冲突旧句，不改变Stage2方案审查规则 | S01 |
| S04 | 新 `scripts/measure_review_state.py.main` | 匿名化run导出/固定任务标签→JSON+Markdown测量摘要；只读、流式/按引用，缺失数据标unknown，拒绝混合protocol比较为同组 | S01 |
| S05 | `scripts/field_acceptance.py.main`（已核验现有入口） | 新真实宿主worksheet包含局部修复/短屏障/unknown/最终Stage4场景与各指标；旧延期路线单列，不自动部署 | S02、S03、S04 |

模块CLI测量需区分wall/CPU、协调/验证重叠，固定样本ID而非按成功挑选。外部模型差异单列，不采用不同模型结果宣称协议提升。发布仍需后续用户授权，不在本Ticket安装本Mac。

## Verification references

V01：manifest内列明的全部相关runtime/package测试经 `scripts/run_tests.py`，最终 `scripts/validate.sh`；若占锁报告owner并结束该次验证。

V02：`python3 scripts/build_skills.py --check`、静态repo核验与版本清单；外部生成包只在实施时产生，本次规划不生成。

V03：真实host按PRD两个Ticket/四片样例：一处局部bug、一处共享fixture变动、一处私有plan revision、一次未知原调用、一次归档后恢复；保存native IDs/停止证明/包digest，并验证Stage4接收当前候选。现场未执行就明确标记未验证。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。


## Matt 1.3.1 基础兼容校准（2026-10-07）

先继承 main `70822bc` 的路径与外部依赖约定。本项目新增领域术语只写 `GLOSSARY.md`；消费项目既有 canonical `CONTEXT.md` 仍有效，阶段提示与文档索引传递选定路径。上游静态兼容基线为 `dd400c3ad65e57c06f05e832e0aac92c7992f34d` / `1.3.1`，动作名称和六 Ticket 合同不变。

V02/V03 必须区分 main 历史宿主 preflight 与本分支新包：核真实安装的宿主适配入口及 exact package identity，main 的十项 action preflight 不替代新 control12/schema7/v2 计划下的原生交接或完整端到端样例。仅更换词汇表路径无需重跑 T01/T02 全套，也不重标其原审查源点；此兼容增量的构建、静态和包回归单独记证。原完整 validate.sh 和未验证原生现场仍按本 Ticket 执行。
