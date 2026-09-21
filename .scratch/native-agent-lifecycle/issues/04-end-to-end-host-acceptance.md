# 04 — 验收全阶段生命周期与生成包一致性

Status: ready-for-agent
Lifecycle: completed

**What to build:** 在隔离仓库用真实 runner/C/B/D 完成阶段 2→3→4，核实原生角色跨轮、独立审查、嵌套停止和唯一发布；让阶段模板与自包含生成包说明同一用户行为。依据 Spec 全部 D1–D7/A1–A6；不是重新选择方案。

**Blocked by:** 02 — 在保留宿主时回答、暂停并停止嵌套角色; 03 — 宿主丢失或回执中断后保留原身份与副作用

- [x] 同步 solution-designer、Implementation Dispatcher/Execution Agents、双轴 review 和 Closure Agent 的 wait/continue/stop 语义，以及 pause/needs_input 前台持有和 queued 答案说明。
- [x] 临时仓库全链测试核实同一 Flow Worktree、精确候选双轴审查、规划发布、最终 merge/cleanup 各一次；重复 resumed/completed 不重放。
- [x] 真实宿主用实际 runner/前台宿主产品适配模块和候选包验证单层跨父轮 running、同身份 followup、父先结束/子仍运行、配置继承和本地资源清理，保存原始事件及 CLI 版本；嵌套停止在真实生产接口下做确定性测试，真实嵌套集成单列外部依赖待验证。
- [x] 将宿主进程退出后的不可恢复能力与成功案例分别报告；明确无现场证据的项，不能以测试替身回执宣称真实通过。
- [x] 确定性构建和 build check、项目全部要求的运行时测试、repository validator 通过；生成包资源闭包和 C 新兼容键一致。
- [x] 没有安装、部署、生产下载任务恢复、外部 Matt/治理插件修改或旧运行迁移；本仓必需验收失败按 Spec fail-closed 保留现场并报告；外部嵌套集成未验证不计通过，也不阻断本仓交付。不扩展为 daemon 或换角色。

## Comments

由原生 to-tickets 行为生成；连续模式下粒度与阻塞关系已经按 Spec 中的切片决定确认。完成实现后才记录 Lifecycle: completed。

来源采用 Spec 的新冻结需求 commit `906efc37037c11d4a8b5ee99eac301079368fb8e`。实施遵循 Spec Further Notes 的 allowlist；运行时模板/协议及生成包属于实现候选，不能推迟到归档修补。

Closure: The three-stage runner/C/B/Git chain with substituted host transport, exact review fixed point, unique publication/cleanup, generated packages and full validation passed. Real single-layer lifecycle and stop/resume/cancel passed for the accepted module; real nested integration remains an external pending dependency.

Accepted candidate `35d0c24d3729d6487751d80baabea1310a69a43a`; independent Standards and Spec accepted. Full suite: 671 tests OK, one existing skip; 28 host and 6 pipe/priority focused tests passed. Exact evidence and limits are recorded in `../PRD.md`, section "Stage 4 implementation acceptance and closure", and `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/candidate-delivery.json`. Checkmarks certify the agreed repository scope, not real nested integration; that external item is not passed and does not block local delivery.
