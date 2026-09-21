# 01 — 贯通宿主注册证据与 Flow 入口

Status: ready-for-agent
Blocked by: None

**What to build:** 用户从宿主项目运行到独立 Flow Worktree 时，runner、carrier 与原生角色消费同一当前注册证据，入口同时独立核验真实工作区。

Spec: ../PRD.md — D1、D5、D6；AC1。

- [ ] 复用 registry_input 与原始宿主项目身份，补齐 registry_context 的项目、controller、查询回执、registry digest；由脚本重读并核验，禁止 Agent 摘取或目录 fallback。
- [ ] 内联与文件输入互斥；同目录直接查询保持支持；跨目录委派缺证据在副作用前失败。
- [ ] 真实 cwd 仍与 Flow binding 核验，当前注册变化不替换 pinned package。
- [ ] 贯穿 runner prompt、stage transfer/dispatch 与 Stage 2/3/4 native bootstrap；同步相关说明、协议兼容键及生成包。
- [ ] 按 T1 在真实生成 CLI/临时 Git 链上覆盖正确来源、缺失/过期/跨项目、刷新/禁用、pin 漂移与伪造 cwd，不能只测 helper。

Testing seam: Spec T1 与 AC1 行；首个 red/green 取宿主项目与执行 cwd 不同的完整入口链。定向回归通过即可成为本切片 checkpoint，全量验证留到审查收敛后。

## Comments

原生 to-tickets 已发布；方案连续模式已回答粒度与依赖问题。与 02 行为独立，若共享脚本或协议文件由同一 dispatcher 串行集成。
