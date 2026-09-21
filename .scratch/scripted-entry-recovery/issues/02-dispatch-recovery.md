# 02 — 在原控制记录中核验并接管未提交成果

Status: ready-for-agent
Blocked by: None

**What to build:** 原执行者不可续接且旧写入者全部停止时，Controller 能在同一 Flow Worktree 保留已提交及未提交成果，完成唯一、可重入的 Stage 3 接管。

Spec: ../PRD.md — D2、D3、D4、D5、D6；AC2、AC3、AC4、AC5。

- [ ] prepare-dispatch-recovery 使用原 Controller、原 attempt、真实停止/调用回执与实际 Git 快照，输出 recovery_id、digest、归属与待重验范围；缺项时不派发。
- [ ] accepted fingerprints、未接受 allocation、dispatcher 自写成果分别归属；未知/冲突/保护或越界字节拒绝，失败不改 HEAD/index/内容，不强制先 commit。
- [ ] Controller 精确快照决定之后先保存唯一 intent，再创建无写权限的准备态 successor；未知创建只对账，激活前再次核验。
- [ ] recover-dispatch 原子消费准备结果和真实 replacement ref，保留旧身份/回执，冻结新 attempt；相同请求 ACK，冲突拒绝，重新中断不得重复派发或激活。
- [ ] 剩余范围及旧 allocation 待重验规则可被 successor 执行，旧测试不自动成为新候选通过结果；双轴审查和最新最终验证顺序保持。
- [ ] originating-task、control/progression 与 stage bootstrap 同步新恢复契约，协议及生成包一致。
- [ ] T1 真 Git 覆盖混合未提交成果、文件模式/删除/新增/index 漂移、未绑定调用、prepare 后漂移以及每个恢复落盘边界，外部调用数量证明无重复。

Testing seam: Spec AC2–AC5 各行；首个 red/green 是 accepted 与 dispatcher 未提交成果共存时由生产 control/Git/C 链执行接管。宿主丢失不属于可接管的充分证据。

## Comments

不新增调度器、账本或真实跨宿主恢复能力；不放宽停止/来源认证。与 01 无行为依赖，但共享协议兼容版本统一由 dispatcher 集成。
