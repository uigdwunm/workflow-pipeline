# 02 — 停写后修订范围并保留接受历史

Status: ready-for-agent
Review: tickets-review-20260926-01 — accepted
Blocked by: 01
Spec: ../PRD.md — D4、D1/D3 的所有权边界；Testing Decisions
Requirement: ../../../docs/requirements/2026-09-26-stage-scope-finalization.md

**What to build:** Controller 能补齐空范围或非空漏文件，并在同一工作区继续。尚未执行可直接校验后修订；已有执行者则先关闭旧写入/继续权限、结清调用、验证真实停止和 Git 快照，再激活新 revision。中断可以恢复，旧成果、接受记录和审查固定点保持原貌。

- [ ] scope-revise 显式绑定旧 revision/digest、原需求、规划提交、binding、逐项差异及 Controller 决定；需求/已接受业务方案变化仍先走原变更决策。
- [ ] proposed→stopping→quiescent→authorized→active 的每步在原 C checkpoint 持久化；进入 stopping 后关闭 allocation、继续、候选接受、出版和下一阶段入口。
- [ ] “未启动”必须证明确实没有 launch intent、未决创建或宿主调用，不能把无 ref 当作未创建。
- [ ] 已启动场景覆盖 Dispatcher、Execution Agent、reviewer 和 direct 作者；running/unknown/陈旧 inventory/未决调用均阻断，原生停止与完整 calls 才允许前进。
- [ ] 快照覆盖原范围和拟增路径的存在、模式、内容、HEAD/index，以及逐 commit 路径改动；拒绝旧授权外已产生的改动，不能追认越权。
- [ ] 删除或转交有未接受工作/旧分配/尚需覆盖行为的路径时拒绝；合法 absent 新文件可加入；字节未变的已接受成果可保留引用。
- [ ] 原 candidate/review/verification/fixed point 对象不改写，只撤销其对新 revision 的有效性；新审查必须绑定新 revision，即使候选 SHA 相同。
- [ ] 已接受 Stage 3 且无 Stage-4 intent 时创建新 revision attempt；已有 Stage-4 writer/出版事务不能在此原地修订，先走原恢复路由回到合法 Stage-3 修复点。
- [ ] direct 的旧 scope_digest 评估失效，按原政策重验；用户 pause/cancel 不被范围修订隐式解除。
- [ ] 原 Dispatcher 能继续时沿用原身份，不可恢复才进入原 dispatch recovery；本操作不暗中派发替代 Agent。
- [ ] 重复 revision ID 同内容 ACK、异内容拒绝；所有控制/ledger/宿主事务断点从原 transaction 恢复，零重复接受、派发和出版。

**测试 seam：** 01 的真实 C/B/Git 链增加原生命周期响应，覆盖未启动修订、运行中停写、已接受字节保持、逐 commit 增删抵消、未跟踪冲突、direct 和附着 CAS。对持久意图、调用、回执、激活间逐点故障注入，断言旧历史不变、新分配范围及恢复次数。

**完成边界：** 新协议正常和修订路径可端到端验收，随切片同步相应生成资源。03 不依赖本 Ticket 的代码；04 再验证旧接管完成后使用本修订路径的组合。共享文件由 Dispatcher 串行整合或精确拆分，不允许并发覆盖。权限仍完全受 Spec 精确清单限制。
