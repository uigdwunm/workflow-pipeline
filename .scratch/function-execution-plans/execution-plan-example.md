# 代表性 Ticket / 函数执行与内部修订样例

Publication: published — illustration only
Review: accepted fep-solution-review-20261001-01
Parent Ticket: `issues/02-controller-plan-revision.md` (`FEP-02`, plan_version=1)
Authority: `PRD.md` D2–D4/N5；此文件是阅读样例，实际计划仍由上述 Ticket 的正式 block 拥有。以下 revision/ref/hash 是情景代号，不冒充真实宿主回执或现有封存授权。

## Ticket 合同

交付行为：原 Controller 可在确认合同和业务影响范围内修订函数计划并同步必要文件；修订后才派发，旧权限不会继续写。验收：全停止且原 calls 核对完成之前没有 release；只允许规划区域变化，implementation/index/冻结需求不变；重复请求恢复唯一 current revision；暂停取消不能被修订恢复。

## 三个相关函数组成一次分配

| 函数计划 | 能力与过程 | 关键 I/O 和失败 | 前置与验证 |
| --- | --- | --- | --- |
| FP2-01：Progress.prepare_plan_revision | 检查原 owner/current authority，关闭新 allocation，停止并核对原 Dispatcher/所有 writer/review/check；冻结 snapshot 和 contract，保存 Controller-only 精确 planning release | report/current authority/actual stop facts → stopping 或 prepared；unknown/old revision/contract外没有release | Stage-2计划目录已封存；与 FP2-03 共用 V2 行为场景 |
| FP2-08：verify_plan_revision | 比对 only-planning commit、父子树、branch/index分区及全部 implementation/protected 指纹，提取当前合同 projection | old snapshot + actual Git/window → derived verified facts；一项意外文件/合同改变就拒绝 | prepared release 后 Controller 精确规划提交；共用 V2 |
| FP2-02：Progress.commit_plan_revision | 用真实 derived facts 验新计划/manifest闭包，持久 transaction，再一次性 activate 新 authority/结果资格 | prepared + current plan commit → activated revision；planning-committed后失败保持 barrier | 先verify，后activate；V2 验证旧权限拒绝、新权限可派发 |

这三项属于同一责任，可以由一个 Execution Agent 实现；共享 source 文件的其他 Ticket 分配按顺序串行。正式 Ticket 的完整顺序还含 handle/advance/control/attached 接线，图表只摘取代表性能力，不替换该 block。测试先从真实 C handle 的 missing plan/contract drift/replay 场景取得 red，再用实际 wiring green；不要求每个函数一项测试。

## 合同内修订：拆出私有校验 helper

实现者发现 FP2-08 中“合同 projection 核验”和“implementation index 分区核验”分开能让失败定位更准确；对外 verify_plan_revision 的输入、输出、错误类别及调用者不变，责任仍是同一 Git boundary。Execution Agent 停止这组工作并把真实调用链、失败场景和拆分理由交 Dispatcher；Dispatcher 上报原上层，自己不改计划/代码。

原 Controller 核对依据后自行把 FP2-08 的过程更新，保留其 stable ID，为拟新增 `verify_revision_contract` 与 `verify_revision_index`（同一原 file）增加两个 plan IDs，将 Ticket plan_version 增至2。如果确需把 helper 放进新 private 文件，先给 exact新路径、责任和 original absence fingerprint，并列出其所有生成包副本及对应 package.json source→outputs；在同一 manifest activation 封存后才可写该文件。无功能/验收/公共契约/业务职责变化，不产生用户审批或状态报告。

全停止窗口即使这只是一个函数拆分也适用：所有当前 writers及统一验证共用 HEAD/index和整树 snapshot。已接受 FP1-* 计划不变、依赖/read_only/verification未变、bytes/mode与snapshot一致，可保存 carry-forward 引用；已完成但受FP2-08影响的bytes仍保留，只是待复核。新分配带 plan_version=2/current authority digest/new write_release，必要 adopt_paths由真实 Agent验证后验收。原 Agent 的 version1 release/晚结果和旧 review仅为历史，不能重新写或证明新候选。重新运行affected checks、两轴review和full final validation。

## 需要用户决定的相似提议

若拆分要求改变公共 JSON 字段、允许Dispatcher写规划、扩大到其他业务模块、修改验收或采用新的通用调度服务，则合同/影响边界已变。原上层先说明具体before/after与替代选择并询问用户，affected任务保持暂停；不能写内部计划后再报告。原 frozen source与launch保留；若用户选择改变合同，先由原来源责任方完成新冻结/方案与授权，再通过现有受控恢复进入，不用internal transaction改原需求或迁移旧运行。

## 中断与重试

prepared前unknown writer/call：只lookup原identity/action；无release。Controller提交响应丢失：检查原commit意图、parent/tree/path与snapshot，确认同一提交后才进入planning-committed；不重复commit。activation响应丢失：从原control transaction读回，已有revision仅ACK。paused保留事务和字节，等待明确resume；cancelled撤销window、保留可审计字节，不由revision复活。原host不可核验时阻断，不开新host伪造恢复。
