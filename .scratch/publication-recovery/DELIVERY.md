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
  ignored 文件和同名重建资源。使用非强制 remove 和 branch -d，不 prune。
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
