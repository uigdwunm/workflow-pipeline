# 重启后验收交接

实现来源：本目录 PRD.md；安装结果：同目录 deployment.json。
本地测试已通过，真实 Hook 与原生子任务验证尚未执行。

## 新任务配置

重启 Codex，在实现任务之外的新任务中进行验收。按治理插件仓库 AGENTS.md，
配置 gpt-5.6-terra / high；用户明确指定其他配置时记录覆盖。先确认实际配置，
无法确认时保持未验证。不要继承、复制或设置旧任务的 session 环境变量。

## 验收步骤

1. 读取 workflow-pipeline 与 subagent-governance 开发仓库的 AGENTS.md、
   当前注册的治理 skill，以及本任务 Hook 注入的 session 和 CLI entrypoint。
   检查部署结果为成功，内部五个技能与已提交源的内容哈希一致；解析器返回
   thread-settings-v5。核对当前插件版本为 0.4.0+codex.20260906031301。
2. 运行 resolve --current 与 verify --current，验证当前任务精确设置回执。
3. 按治理 skill prepare 一个只读隔离子 Agent；模型与 effort 默认继承。
   子 Agent 只验证自己的设置与身份，返回脱敏的公开回执，不编辑任何文件。
   检查 spawn_args 使用 fork_context:false，消息首行为生成的治理身份。
   原样派发，使用这次返回的 agent_id 立即 confirm，不猜测身份或更改参数。
4. 验证 PreToolUse 已 claim，confirm 已绑定唯一 target，子任务设置符合披露，
   接收终态，原生 close_agent 并关闭治理记录。若消息在 Hook 中不可见或 claim
   缺失，保留异常与真实 target，不自动重派、不手工补造 claim。
5. 在独立临时 Git fixture 中准备并提交一个最小需求，冻结路径、commit 和
   SHA-256。按 Stage 2 的真实入口与治理流程，验证设置检查、worktree 绑定及
   SOLUTION_DESIGN_STARTED。该 fixture 仅用于启动验证，禁止发布真实业务规划
   或推进原讨论状态；按协议收尾测试子任务和临时工作树。
6. 把配置、设置回执、Hook claim/绑定/关闭结果和 fixture 结果写回本目录验收
   记录，只保留脱敏结构。完成这些检查后才能标记兼容性修复完整验收。

真实原方案任务恢复是独立步骤：先在原任务核对其冻结需求及已有检查点，保留
原授权与确认状态；本交接不授权代写方案、扩大需求或重做已完成阶段。
