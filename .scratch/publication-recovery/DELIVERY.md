# D：发布、对账、清理与完成核验

Status: ready-for-human

## 授权与范围

本任务先澄清，再提交完整方案，经用户明确确认后实施。开发基线为
`0b431719592af4cf2704eddd9f917e54555089d7`，分支为
`codex/publication-recovery-d`。不合并 main、不 push、不部署、不清理开发
Worktree 或其他任务。外部 Matt 发布与独立 Stage 1→2 producer 不在范围内。

## 实现与权威

- `supervision_protocol.py` 复用现有 publish-planning / complete-worktree；
  callback 把 refresh、prepared、merged 和资源移除事实交给已有 checkpoint
  owner 持久化。没有第二套账本、循环或全局锁。
- `reconcile-publication` 核验原请求的精确父链、候选树、target ancestry 和
  scope，区分未发布、已准备、Flow 待推进、清理待完成与已完成。
- `cleanup-only` 只处理已验证发布的剩余资源。检查目录/Git directory
  device/inode、分支 ref/reflog 身份及实际内容；保留新修改、未跟踪文件、
  ignored 文件和同名重建资源。使用非强制 remove，以及保留 branch -d 安全
  条件的引用事务，不 prune。复审修复的具体删除边界见下文。
- C 新增 publication_candidate observation 与 publication-readiness decision，
  消费原 Controller readiness 和原生 stopped receipt。Stage 4 区分已验收
  实现与含 closure 文档的最终 tip；后者只能增加原 closure_paths。
- C receive-publication 显式保存 stage-owner-publication intake：原角色候选、
  原始 host receipt/readiness、独立 Git facts、组合者和最终 B message。
  不伪造发布后新的原生响应；B receive → Controller accept → 原 Phase 收尾
  保持分离。references/checks 是来源说明，不是脚本创造的审查或测试证明。
- C resume 先对账原动作，已发布不重发；暂停/取消保留原门禁。合并回执丢失
  时也不得误返回实施。B intake 中断重放原事务，不换 port 或幂等键。
- `verify_completion` 与 runner 最终及重复 completed 响应复查 B、控制、Phase、
  发布和实际清理。历史 planning worktree 已删除时只验证其不可变交付链。

## C 接入表

| 接口 | 输入/来源 | 输出与副作用 |
| --- | --- | --- |
| observe.publication_candidate | 精确 tip、artifacts/checks、原 stopped receipt | 保存原候选，提出原 readiness 记录 |
| decide publication-readiness | 原 Controller 的精确决定 | 保存授权消费；不替代最终验收 |
| publication | 原 tip/reference、planning_paths 或绑定 target | 保存精确请求/中间事实，调用既有 Git 原语 |
| reconcile-publication | 原 checkpoint，无替换请求 | 只读核验实际结果，不推进或清理 |
| resume | 原事务和仍有效授权 | 对账后完成合法剩余动作 |
| receive-publication | 原 native evidence + 独立 Git 事实 | 组合并保存来源，交 B 核验；保留最终 acceptance |
| verify_completion | 持久化最终接受及前序链 | 复查必要发布、控制/Phase 和资源事实 |

## 兼容与边界

`workflow_progress` 改为 `workflow-progress-v2`，包 supervision compatibility
改为 `flow-worktree-v2`；外层 runner 仍为版本 3。v1 成员拒绝在 v2 中迁移，旧
运行使用原 pinned 包；构建更新生成包，不手改生成副本。A 的需求身份、冻结
语义和 B 的交付字段未变，Stage 1→2 的单父需求交付限制未扩展。

独立或讨论附着的托管阶段共用本机制，连续与逐阶段保留原授权策略。独立
Stage 4 的无文档变化路径仍不创建 Worktree 或提交；其原窄范围文档流程不
伪造 Stage 3 交付或讨论 Phase。底层原语不提供语义授权，恢复需要原 owner
保存的精确事实。任意外部 Git 写者不受进程文件锁控制；身份/内容漂移会阻塞，
真实宿主停写证明仍由适配器提供。

## 验证证据

验证均使用隔离临时 Git/ledger/checkpoint，真实宿主身份仅使用明确的 fixture。

- 每个最终发布断点：prepared、merged、worktree-removed、branch-removed。
- 合并成功但 merged 回调/最终回执丢失，target 随后推进仍取原 merge。
- Stage 2 refresh 已发生但 prepared 未保存、发布后 Flow 尚未推进。
- 目标变化阻塞、清理失败只恢复清理、新文件与 ignored 内容保留。
- 同名分支在相同 SHA 重建仍不能被删除。
- CLI inspect 不清理；cleanup-only 需要精确原发布证明。
- C 未验收/idle/错误 readiness 被拒，暂停只对账、显式恢复后推进。
- Stage 4 文档 tip 与 B 实现 candidate 分离，原 raw receipt 保持不变。
- B intake 回执丢失重放原事务；发布后问题报告不能误返回实施。
- 整体完成缺失 publication/control/cleanup 时拒绝；正常真实临时 Git 链通过。

最终 `./scripts/validate.sh`：499 项，498 通过、1 项既有不适用跳过，耗时
757.575 秒；五个 Skill 校验、构建一致性与仓库校验均通过。仓库校验返回
issues=[]，76 个引用、5 个 Skill、25 个测试文件。原始输出见
[VALIDATION.txt](VALIDATION.txt)。`git diff --check` 通过。

新增 16 项行为回归：8 项 Git 恢复、5 项 C/B/旧记录兼容、3 项推进门禁与完成
核验。独立只读审查发现的“合并后回执未存即误返回实施”已修复，真实 Git
故障回归确认返回 already_published。暂停/取消入口对 v1 记录的拒绝发生在
写入之前，测试验证原始记录字节不变。

真实 host 创建/异步 ready、停止、跨 host lookup、外部 Matt 调用、远程发布和
本机部署未现场验证。最终实现提交由协调任务独立复审；本任务未合并或部署。

## 复审修复：基于 c74dd04

独立复审未通过原候选，用户授权修复两项问题；下列修复没有新增发布、合并
或部署授权，仍在原分支完成。

### P1：B 结果已保存，C 本地消费未完成

Stage 2/4 的真实临时 Git 回归在 `Progress.apply` 保存 receive 结果后中断。
原实现的 resume、receive-publication、advance 均不能进入 acceptance，两个
测试的六个入口子场景失败。相邻的 B accept 已保存窗口也取得两个失败回归。
红色输出见 [REVISION-RED.txt](REVISION-RED.txt)，仅将本机工作区前缀替换为
`<worktree>`；不将旧通过结果当作本次修复证据。

`recover_publication_intake_step` 根据已经持久化的同一 B delivery 补 C 本地
步骤，不再调用 B 或发布器；已有 pending 的 ID/subject 保持不变。B accept
之前先保存不可替换的原 Controller 完整 decision。结果未保存时重放同一个
原事务；结果已保存时消费同一个 decision，不重放 B，也不生成新的验收决定。
已有 accepted 和逐阶段 successor pending 不覆盖。pause/cancel 门禁先执行。

回归覆盖两个阶段的 receive/accept 保存前后中断、三个恢复入口、重复调用、
错误替换原 decision 被拒、逐阶段 successor ID 保持和暂停后取消优先。

### P2：最终分支删除需原子保护

原实现的四个真实 Git 交错回归失败：正常/恢复入口在 Worktree 删除 callback
后同名同 SHA 重建分支，以及末次 SHA 检查后更新到另一个已合并 OID，都会
被 branch -d 误删。

现在使用实际 Git 2.54.0 (Apple Git-157) 的 update-ref --stdin 显式事务：
start → delete(原 candidate OID) + verify(删除依据引用) → prepare → 锁内
原 ref/reflog 资源身份与非强制条件复核 → commit。prepare 锁住分支及作为
已合并依据的引用，防止核验后 OID 变化。相同 OID 的分支重建仍必须通过原
device/inode/ref/reflog 身份检查；不能仅靠 OID 相同删除新资源。

保留上游/HEAD 已合并检查、target ancestry、其他 Worktree 使用检查，并
保守阻塞任何活跃 rebase/bisect/sequencer 等历史操作。支持 loose/packed refs，
拒绝 symbolic branch refs。Git 的 ref 锁保护普通 Git 引用写者；不是锁住任意
外部文件写入或通用 Worktree 调度器，原停写与控制权合同保持。

prepare 前、prepare 后、commit 后回执丢失分别做真实 CLI 故障注入。未提交
事务通过 stdin EOF 由 Git abort；不删除其他进程锁。未确认通信结果只允许
按实际 Git 状态对账。已删后同名 ref/reflog 重建会被保留并阻塞完成。最终
完成检查要求原 Worktree、branch ref 与 reflog 均消失。分支配置刻意保留，
不声称清除所有配置，不为了清理旧分支而删除后来用户配置。

证据包含 callback 后及 prepare 前同 SHA 重建、prepare 持锁后 competing
update-ref/branch -d/target 更新失败、事务中断与锁释放、外部锁保留、其他
Worktree、未合并 upstream、活跃历史操作、旧分支删除后新资源保留和配置保留。
同名资源复用检查还发现，悬空 symbolic ref 不能仅凭无法解析 OID 就报告删除。
该用例也先取得失败回归，再把物理 ref/reflog 路径存在性纳入完成条件；保留
新资源并返回 cleanup-pending。

本次修复最终 `./scripts/validate.sh`：528 项，527 通过、1 项既有不适用跳过，
耗时 888.333 秒。五个 Skill、构建一致性与仓库校验全部通过，issues=[]。
相对 c74dd04 新增 29 项回归，原有保存前中断用例继续保留；真实 CLI 和故障
注入均通过。最终完整输出及 Git 版本见
[REVISION-VALIDATION.txt](REVISION-VALIDATION.txt)。`git diff --check` 通过。

真实宿主与跨宿主能力、其他 Git 版本及其他 refs 存储后端仍未现场验证；本轮
并发证据来自隔离临时 Git 的 loose/packed refs 和明确的宿主 fixture。修复
没有修改 A/B 公共权限、两种 candidate 身份、pin 策略或包兼容标识，也没有
迁移旧运行。本候选仍须协调任务复审及用户另行授权合并，未合并、push 或部署。

## 暂停恢复修复：基于 980a2c4

上一轮 receive 已保存/C 未消费以及分支删除问题已由协调复审关闭；仍有暂停
后的未保存 B 事务恢复问题。本轮仅修复该状态转换，不修改已验收的分支删除
原语，也不声称这是上一提交新引入的回归。

红色证据：Stage 2/4 × receive/accept 在 apply 保存前中断，再 pause/resume，
四条路径全部失败：receive 被 stop gate 拒绝，accept 再次 deferred 并留在
paused。输出见 [PAUSE-RED.txt](PAUSE-RED.txt)，工作区前缀替换为 `<worktree>`。

`resume` 现在先保留原 runner_request、控制恢复及取消处理顺序；仍处于
pausing 时只处理原停止/查询动作。只有 paused 且已有 stopped 证明时才解除
暂停，恢复合法状态并保存，然后用原精确 B envelope/Controller decision
重放或消费事务。没有移除全局 stop gate，没有把普通 advance 或重复回答
当作显式恢复。延后输入仍通过原入口消费，不在消费入口接手之前先单独保存
并丢弃恢复线索。接口、兼容标识、pin、角色及权限均不变。

新增八项矩阵用例覆盖 Stage 2/4 × receive/accept × 保存前/后，并检查：

- 中断、pause、resume、重复恢复的接收/验收结果及精确身份；
- 未保存时只重放一次原请求，已保存时零次 B 重放；
- 暂停解除已保存后再中断，仍能恢复原事务；
- running/pausing 缺少停止证明时不能重放，原 stopped 回执才能结束暂停；
- 普通 advance、重复回答和仍存在的 runner pause request 不能解锁；
- 直接 cancel 或 pause 后 cancel 都保留取消意图。Stage 4 在 Worktree 已删除
  后可能仍需原控制恢复；测试不把这种 blocked/cancelling 误报为取消已完成；
- pending/decision/transaction、载体、发布请求及 Git 结果保持，不重发或重派。

最终 `./scripts/validate.sh`：536 项，535 通过、1 项既有不适用跳过，耗时
1053.846 秒；五个 Skill、构建一致性与仓库校验全部通过，issues=[]。
完整输出见 [PAUSE-VALIDATION.txt](PAUSE-VALIDATION.txt)。新增八项矩阵回归及
原有十七项暂停相关定向检查均通过，`git diff --check` 通过。

真实宿主/跨宿主能力仍未现场验证；本轮真实 Git 与 checkpoint 配合明确的
宿主 fixture 验证。生产修改仅涉及共享 resume 的恢复顺序，分支删除实现及其
测试不变。本候选仍需协调复审和用户另行授权合并，未合并、push 或部署。
