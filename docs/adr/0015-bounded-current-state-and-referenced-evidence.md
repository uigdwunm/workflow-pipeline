# ADR-0015: Bound current control state and reference immutable evidence

Status: Accepted
Review: accepted — 当前对话用户接受方案并选择连续模式
Revision: 2026-10-07 — 用户明确收缩本轮；保留归档/有界当前，延期 GC/物理配额
Scope: 新运行；不迁移旧记录，不替换旧Pinned Package Identity

## Context

生产v17检查点达325MB，新v19小样例仍约39MB。重复port/result.context/历史内嵌导致读写放大；单纯压缩或把完整副本搬入blob不能避免二次增长。控制权威有standalone checkpoint和既有ledger-backed两种真实owner，另建可写控制账会引入冲突。

## Decision

保留原Controller及权威提交位置。当前root有固定容量，只引用活跃Ticket/工作批次、未决事务和当前权限；有意义事件分段，原证据内容寻址。内部结果在构造时就是有限projection/delta与共享合同引用，禁止先展开全历史再外置。已完成握手去掉派生副本，只保留真实原调用、身份、结果和必要适用性事实。

独立内部store模块封装有限读取、CAS提交、引用验证、归档与容量规则，不提供全hydrate默认接口，不引入后台服务。standalone原checkpoint指针、ledger-backed原ledger指针分别为唯一当前权威；runner只更新镜像。对象先持久化，最后原子切换权威指针。root只保留当前/上一有效代及明确未决/交付pin，不递归持有历史root链。

副作用前保存原 intent，调用结果未知不重发。幂等 receipt 按 hash 路径直接查找，历史索引不常驻。归档和删除分开：完成 subject 退出活跃 root，原事件、receipt、claims、archive 和恢复证据保留。本轮不自动删除临时/孤立历史；年龄/两代扫描 GC、整个 run 物理配额、双预留与宿主 log spool 延期且未验收。当前记录预算不运行中增容，不能通过删除有效证据腾空间。IO 故障沿原 fail-closed/原调用恢复语义处理，不承诺磁盘耗尽下可用性。

任务管理只维护一个有界当前入口，阶段完成后替换状态并移出旧下一步；连续性文档与历史报告只作引用，不维护第二份当前指令。模型输入和结构化权威核验分别计算一次交接/恢复的实际总读取，包括展开的依赖和证据；文件拆小不豁免总读取预算。字段、固定限额与整理触发见 [执行规则](../../.scratch/review-state-efficiency/execution-policy.md#当前记录与读取预算)，运行时原提交权威及原证据保留规则不变。

## Alternatives and consequences

单个大JSON最易理解但每次全读/全写；仅拆文件但全hydrate不解决恢复成本；SQLite可提供事务但引入与现有文件/ledger跨介质协调，并不能自动决定证据有效性。本方案复用现有文件锁、fsync/replace和权威owner，代价是必须明确CAS提交点、引用图与故障恢复。

该策略有界的是当前控制和日常恢复；本轮历史磁盘仍随有意义工作增长，没有整个 run 的物理总量保证，不承诺无限单run吞吐。GC/物理配额专项和万级物理增长基准属于后续独立范围。旧决策见 Git `35d5a57876badce5a4beb01c99ac2a5db62c404e`，冻结需求不改写；延期条款不得重标通过。固定当前预算仍须实际证据核验。

完整预算、布局、失败状态与验证见 [.scratch/review-state-efficiency/PRD.md](../../.scratch/review-state-efficiency/PRD.md)。本 ADR 按后续用户指令收缩本轮边界，保留已验收成果；实现仍暂停，不授权安装或旧记录清理。
