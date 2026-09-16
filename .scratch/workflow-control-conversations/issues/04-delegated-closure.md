# 04 — 委托归档并由总控验收整体交付

**What to build:** 总控将已审候选交给原生归档者收尾、发布及清理，核验整体结果；实现问题退回3，发布后的清理失败不重复发布。

**Blocked by:** 03 — 单一原生调度者完成实现与故障恢复。

Status: ready-for-agent

Spec依据：I6；需求场景7/8与D6–D8；测试T8/T10。依赖03候选及审查handoff；复用supervision真实临时Git及runner进程边界。

- [ ] 交互/scripted只委托1个Closure Agent，要求候选、review、scope和binding一致。
- [ ] 归档者不修代码，实现缺陷/需要实现处理的target advance返回总控→3。
- [ ] 总控核验merge ancestry、changed paths、测试、review与资源状态；partial不报完成。
- [ ] 已merge仅恢复cleanup，不重复complete-worktree；未merge保留现有恢复语义。
- [ ] scripted carrier返回后由runner唯一推进，错误或needs_input停止，controller身份持续相同。
- [ ] 交付closure角色、最终验收与scripted边界的待归档文本。
