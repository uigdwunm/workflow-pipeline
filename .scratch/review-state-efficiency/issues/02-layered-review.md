# 02 — 三层审查和可证明的证据继承

Status: ready-for-agent
Design status: accepted — 普通本地连续实现已授权
Blocked by: 01
Spec: ../PRD.md
Scope inventory: ../manifest.json（T02）

## What to build

同一Ticket的两个行为片分别接受一个独立reviewer的规范/局部合同审查，组合后由该reviewer验收完整AC；交付两轴只查跨Ticket、共享状态、覆盖缺口。增量修复只让影响闭包中的证据失效，最终候选必须有完整有效coverage certificate。

## 合同和验收

- [ ] 实现者不能审查自己任何版本的片；reviewer可复用，Ticket验收不强制新Agent。
- [ ] 三层报告明确不同范围；交付不重复整个diff，且每个需求/AC/变更文件有可达证明。
- [ ] 跨commit继承包含原claim及目标候选的指纹/依赖比较；不能重标旧报告commit伪造新审查。
- [ ] 同文件修改、公共接口/共享夹具改变、漏报读取、未知共享影响均按PRD扩大；未受影响另一Ticket保留。
- [ ] 旧lease、缺失原生停止回执、未解决finding、旧验证摘要不能通过门禁。
- [ ] 无集成delta仍首次核对覆盖与共享状态，重复inspect不新派reviewer。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | 新 `review_evidence.validate_claim/affected_claims/applicable_claims/coverage_certificate` | claim+目标subject+限定依赖投影→通过/失效闭包/证书；缺少边按Ticket或交付扩大；大小受store预算约束 | 无 |
| S02 | `execution_plan.validate_ticket_plan/validate_plan_refs/current_accepted_steps/affected_plan_closure/classify_retained_results` | 计划增加行为片分组、AC/verification/read/shared-state关系；v2 catalog产出可机械比较索引；未知依赖拒绝继承 | S01 |
| S03 | `workflow_control.validate_review_axes/validate_review/validate_reviewable/validate_deliverable/_transition_single` | local-review、ticket-accept、delivery-review行动→三层状态与claim refs；原candidate全量两轴相等条件替换为适用性+完整覆盖 | S01、S02 |
| S04 | `Progress.review_activity/control_action/allocation`、`stage_dispatch.received/accepted` | 原生reviewer身份/停止回执→claim；新action投影按store读取；Dispatcher无自授pass能力 | S03 |
| S05 | `stage_handoff.verify_result/accepted_stage3_progress/_render_verified` | certificate+固定候选+必要最终验证→Stage3/4接收；损坏或过期证据拒绝 | S03、S04 |

S01–S03形成一个片的审查闭环；S04–S05完成原生身份与交付闭环。新增claim不可含嵌套完整依赖claim；仅以稳定引用读取。

## Verification references

V01：新 `tests/runtime/test_layered_review.py` 经真实handle/control/Git跑两Ticket（每个两片），制造一个只影响单文件的局部缺陷；断言另Ticket claim仍有效而当前片不可交付。

V02：`test_review_first_validation.py`、`test_plan_driven_dispatch.py`、`test_stage_transfer.py` 验证完整候选固定点、空delta、不独立reviewer、遗漏文件、同文件其它函数变动。`test_solution_review_progress.py`核验Stage2原有方案审查语义不被Stage3审查层误改。

运行统一runner的对应focused checks。协议说明与模板同本Ticket同步，删掉互相冲突的“每次候选所有代码从头两轴审查”指令；保留角色独立、最终覆盖和完整验证要求。

## Comments

用户已统一接受并选择连续实现；此为普通本地授权，非正式Stage3 handoff。
