# 01 — 原生角色等待期间保留同一前台宿主

Status: ready-for-agent

**What to build:** 让连续运行在同一个前台 app-server 中跨 carrier 轮次完成正常等待和原身份后续工作。用户看到进度，但不会因 continue 退出宿主或重派角色。以 Spec D1/D2/D4/D5/D7、A1/A2/A6 为实施依据；只实现本切片，不临时改架构。

**Blocked by:** None — can start immediately

- [ ] 先用公共 runner + 真实 C 的 continue/running 生产路径复现宿主重复启动，红灯断言基于进程/调用数量与身份，而不是固定文本。
- [ ] 实现 run 内前台 app-server transport 与严格 thread/turn/request 归因；同一 child running 时宿主实例保持 1，后续工作只向已绑定原身份发一次。
- [ ] 正确区分 progress、carrier 终态、subAgentActivity started/completed 与子角色终态；跨旧父 turn 到达的子完成事件仍摄入原调用。
- [ ] 冻结并回读实际 model/effort/cwd/权限配置，支持的字段按本机 schema；不硬编码实验 sandbox，不使用 provider fallback 扩大或改变配置。
- [ ] 引入 version-4 runner / workflow-progress-v6 兼容边界并同步生成包，旧版本只读拒绝，不部署或迁移。
- [ ] 用状态化 fake app-server 驱动公共 start/resume 与真实 C；测试乱序消息、RPC 错误、不支持参数、旧 proof 不能继续，所有 waits 有界。
- [ ] 本切片完成时，在未实现 T2 的 live 用户控制路径必须明确阻塞且保留宿主；不得通过关闭宿主或旧 exec 路径伪装完成。

## Comments

由原生 to-tickets 行为生成；连续模式下粒度与阻塞关系已经按 Spec 中的切片决定确认。完成实现后才记录 Lifecycle: completed。

来源采用 Spec 的新冻结需求 commit `906efc37037c11d4a8b5ee99eac301079368fb8e`。实施遵循 Spec Further Notes 的 allowlist；运行时模板/协议及生成包属于实现候选，不能推迟到归档修补。
