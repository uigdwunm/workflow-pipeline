# ADR-0009: 在前台运行内保留原生 Agent 宿主

Status: Accepted
Lifecycle: completed

carrier 一轮完成并不表示它的原生角色完成；现有每轮 exec/resume 的进程退出边界会失去原生角色。本次选择由已有 runner 在当前运行内部持有一个前台 app-server，跨 carrier 轮次、等待决策和暂停保存宿主，最终释放前按原 C/control 的原身份与嵌套停止证据核验；本机隔离实验已证实同进程跨父轮的 child 完成和同身份 followup。

单次 CLI 不 final 的提示约束不足以机械保护多轮与决策等待，常驻 daemon 则增加需求外生命周期，因此均不作为方案。代价是暂停和等待输入继续占用前台进程，控制命令向同一 checkpoint 排队；宿主丢失后的原生可恢复性未获证明，必须保留现场并停止自动推进。停止证明不等于恢复证明。

ADR-0007 的角色与权威、ADR-0008 的固定包与单一协调状态保持不变。不新增通用子树 registry、迁移旧运行、改部署或自动替代失联角色。完整接口、状态、兼容边界与验收由本次 native-agent-lifecycle Spec 拥有。

本次冻结需求已明确：外部治理上下文缺口交由插件项目处理，本仓真实嵌套集成标为外部依赖待验证，不阻断本仓交付；运行时查停与身份门禁仍严格保留。

## Implementation result

Implemented and independently accepted at `35d0c24d3729d6487751d80baabea1310a69a43a`: same foreground host and original identity across turns, queued decisions, stop barriers covering current/historical reviewers, and preserved uncertain effects after owner loss or partial writes. Full validation: 671 tests OK with one existing skip. Current real single-layer lifecycle and stop/resume/cancel reports passed; the complete three-stage chain uses substituted host transport. Real nested integration remains external pending and nonblocking for this repository delivery. Missing effective permission readback fails closed; loss of the original owner does not imply native identity recovery.

Evidence: `.scratch/native-agent-lifecycle/PRD.md` closure record and `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/candidate-delivery.json`. The first real stop cleanup EPERM failure is retained with unknown cause; one isolated unchanged-module retry passed, without proving the cause fixed. No installation, deployment, external plugin or old business-run operation is claimed.
