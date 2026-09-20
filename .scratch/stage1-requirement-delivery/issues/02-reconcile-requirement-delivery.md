# 02 — 中断和目标推进后复用同一交付

**What to build:** 同一阶段 1 交付在进程中断、hook 失败、提交响应丢失和目标无关推进后能从原 checkpoint 恢复；真实冲突和未知结果保留现场，不重新拷问、重复提交或另选需求。

Blocked by: 01
Status: ready-for-agent
Lifecycle: completed

实施依据：本特性 PRD 的 D2–D4、D6–D7；冻结需求验收 2–5、7。依赖 01 提供真实生产入口和 proof。测试仍从 C/CLI 入口进入，使用真实 Git 和精确故障注入；无需另建 recovery registry。

- [x] C 在副作用前保存原 request/intent/issued，成功后保存完整 result；只占用现有 workflow_requirements 的 delivery 类型交易。
- [x] 丢响应首先 reconcile；匹配 commit 必须核验唯一候选、父提交、全 owned bytes/mode、范围和可达性。
- [x] 原前态/期望态组成的部分写入和本 intent 暂存可续做；冲突或非本 intent 状态停止且不覆盖。
- [x] 提交已完成但工作文档/无关索引漂移时保留有限 completed_evidence；恢复原现场后重用同一提交。
- [x] 目标无关前进且无既有副作用时，先对账再保存关联的新 intent，保留同一冻结来源与授权，无新增用户确认。
- [x] 已交付后无关推进只读复用；owned 变更、非快进、对象不可达、多候选、actor/权限/目标变化明确停止。
- [x] T5–T7 通过，且 T1–T4 重跑覆盖实现改变；重复请求不会创建第二份文档或第二次交付。
- [x] hook/filter 行为、原 pin 和历史 checkpoint 保留，既有 freeze/write 交易回归通过；文档与生成包同步。

完成证据需列明每个中断点实际观测的目标 HEAD、交付对象数量、用户文件/索引保全及恢复结果。仅有 prepared/issued 或对象候选不能报告阶段完成。

## Comments

### 阶段 4 完成证据（2026-09-20）

实现候选：`2233a49b78b18f8711ec25c6d87ea750b8e4b337`。Standards 与 Spec 两轴均已接受同一候选；审查引用分别为 `/root/sg_standard_standards_b5e726d_t_7aa95e6e9616`、`/root/sg_standard_spec_b5e726d_t_b7682d6c274b`。下列勾选表示已接受实现及自动化验证完成。

验证：29 项 requirement-delivery 针对性测试通过；完整 `scripts/validate.sh` 通过，655 项测试、1 项既有跳过，含确定性构建检查、包检查和 repository validator（运行记录 `/tmp/stage1-delivery-gate-full.log`）。恢复快照 SHA-256 与提交 blob 一致，候选工作区干净且 `git diff --check` 通过。阶段 4 仅修改本 PRD 与三个 Ticket 的 Lifecycle、复选框和完成证据，复用上述代码验证结果。

验证边界：真实 Git、文件、索引和 CLI 边界已自动化验证；宿主适配使用测试替身，真实宿主任务创建、消息认证/发送、归档和 UI 未现场验证。未进行本机部署或远程写入。冻结需求与方案语义保持不变。

恢复观测（临时测试仓库的目标 HEAD/对象身份由断言比较，不把临时 SHA 当发布提交）：

| 中断点 | 实际验证的恢复结果 |
| --- | --- |
| intent / issued / delivered checkpoint 保存后 | 每种注入恢复后只有一条交易，基线至目标 HEAD 只有一个交付提交。 |
| 多文档部分写入及部分暂存 | 首份文件保留、第二份尚不存在；恢复补齐后提交仅包含两个 owned 路径；后续 companion 变更阻止复用。 |
| pre-commit hook 失败 | 目标 HEAD 保持原值，索引仅保留本 intent 文档；恢复 hook 后只产生一个交付提交。 |
| 提交成功但响应丢失 | 有限 completed_evidence 指向已存在的目标 HEAD；重试复用该 commit，目标 HEAD 不再次改变。 |
| post-commit 无关文件漂移 | 返回未 verified / 非 downstream-ready 的原 commit 证据；原所有者恢复现场后复用同一 commit。 |
| 丢失对象关联、对象不可达或多个匹配对象 | 保留对象/证据并阻塞，不移动 ref 或重造交付；对象可恢复时按原身份核对。 |

上述结果对应 `test_checkpoint_interruptions_resume_original_transaction`、`test_multi_document_partial_write_and_partial_staging_resume`、`test_hook_failure_keeps_intent_and_staging_for_retry`、`test_lost_commit_response_is_reconciled_without_second_commit`、`test_postcommit_drift_returns_object_evidence_and_can_recover` 及对象对账测试。无关 staged/unstaged/untracked 用户文件和索引保全由正常生产链测试核对；hook 引入的漂移保留并报告，未通过 reset/stash/clean 隐藏。
