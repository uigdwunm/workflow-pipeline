# ADR-0015: Bound current control state and reference immutable evidence

Status: Accepted
Review: accepted — 当前对话用户接受方案并选择连续模式
Scope: 新运行；不迁移旧记录，不替换旧Pinned Package Identity

## Context

生产v17检查点达325MB，新v19小样例仍约39MB。重复port/result.context/历史内嵌导致读写放大；单纯压缩或把完整副本搬入blob不能避免二次增长。控制权威有standalone checkpoint和既有ledger-backed两种真实owner，另建可写控制账会引入冲突。

## Decision

保留原Controller及权威提交位置。当前root有固定容量，只引用活跃Ticket/工作批次、未决事务和当前权限；有意义事件分段，原证据内容寻址。内部结果在构造时就是有限projection/delta与共享合同引用，禁止先展开全历史再外置。已完成握手去掉派生副本，只保留真实原调用、身份、结果和必要适用性事实。

独立内部store模块封装有限读取、CAS提交、引用验证、归档与容量规则，不提供全hydrate默认接口，不引入后台服务。standalone原checkpoint指针、ledger-backed原ledger指针分别为唯一当前权威；runner只更新镜像。对象先持久化，最后原子切换权威指针。root只保留当前/上一有效代及明确未决/交付pin，不递归持有历史root链。

副作用前保存原intent并预留回执/停止空间；调用结果未知不重发。幂等receipt按hash路径直接查找，历史索引不常驻。归档和删除分开：完成subject退出活跃root，默认保留已提交事件、receipt与最终claims；GC仅删满足年龄、完整可达性、无pin及两代标记的孤立/重复证据。容量预算耗尽拒绝新工作，维持原动作恢复能力，不靠运行中增容或删除有效恢复证据。

## Alternatives and consequences

单个大JSON最易理解但每次全读/全写；仅拆文件但全hydrate不解决恢复成本；SQLite可提供事务但引入与现有文件/ledger跨介质协调，并不能自动决定证据有效性。本方案复用现有文件锁、fsync/replace和权威owner，代价是必须明确CAS提交点、引用图与故障恢复。

该策略有界的是当前控制和日常恢复，历史磁盘仍随有意义工作增长并受单run硬预算限制；不承诺无限单run吞吐。保守GC可能无法释放仍有效历史，此时背压是确定结果。全部数值为待验证设计预算，不能当实测性能承诺。

完整预算、布局、失败状态与验证见 [.scratch/review-state-efficiency/PRD.md](../../.scratch/review-state-efficiency/PRD.md)。本ADR已接受，本次连续授权包含新协议实现；不授权安装或旧记录清理。
