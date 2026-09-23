# 首轮工作流简化改造方案

## 目标与范围

减少普通 Discussion Topic 更新时由 Agent 手工编排的事务步骤，消除明确无关文件引起的包预检误报，并降低重复规则文字及措辞测试的维护成本。开发源为 `src/` 和 `build/skill-packages.json`；`skills/` 是构建产物。

本轮保留单一 Flow Worktree、用户已有改动保护、准确候选绑定、未知启动和发布结果的恢复规则，以及清理前确认所有写入者停止。Git 检查点全对象扫描、注册表加载错误的阻断策略、整体状态机和其他阶段职责均不在本轮修改范围内。

## 1. 统一普通主题更新入口

在 `src/shared/scripts/discussion_protocol.py` 增加一个面向普通更新的 `update-topic` 操作。调用者提交现有项目与主题身份、从最近一次 `read-topic` 得到的预期 ledger/topic revision、一枚在重试时复用的 UUIDv4 操作键，以及恰好一条已确认的 typed mutation。成功返回完成的写入结果；后续讨论仍通过 `read-topic` 重新验证完整状态。

入口内部复用现有 `prepare-topic-update` 与 `apply-document-write` 逻辑，稳定地从操作键派生两步各自的幂等键。单入口执行期间两步共用现有短锁；底层操作独立调用时仍各自加锁。prepare 留下的 pending write 继续阻止其他普通更新。调用者不再传递中间 revision、`document_write_id` 或第二枚幂等键。底层操作继续可用作既有调用和异常诊断的兼容接口。

精确重试同一 `update-topic` 请求必须能处理三处中断：payload 已创建但 ledger 尚未记录、ledger 已记录但文档尚未完成、文档已写入但完成记录尚未持久化。第一处只认领与原 mutation 和渲染结果完全相同的孤儿 payload；后两处使用已有 before/after 摘要和 pending 记录完成原写入。若中断后另有 ledger 事件递增全局 revision，单入口明确报冲突；操作者核对其间事件、原 pending write、主题状态与写入权限后，才用现有底层 apply 和当前 revision 完成原写入。复用操作键但更改请求、初始 revision 已过期、主题所有权变化、payload 损坏或文档出现第三种摘要时，保持明确的冲突或恢复错误，不自动采用新状态。

更新 `src/stages/design-discussion/SKILL.md.in` 和直接教 Agent 调用两步操作的讨论/问题澄清参考文档，使普通更新使用单入口。`src/shared/scripts/requirement_prepare.py` 保留“先生成只读 intent，后执行写入”的边界；其附着讨论写入和 reconcile 复用新入口及原 intent，不生成新事务身份。核对操作注册、所有权检查、包内资源引用及旧两步接口的跨包兼容；若 wire/schema 未变，保持现有 ledger 格式和运行中固定包身份，不迁移旧运行。

验收：单次调用完成一条 mutation，重复同一请求只产生一次业务更新；上述三处故障注入均能用原请求恢复；并发 revision 冲突、错误所有者、不同请求复用键及损坏 payload 均停止。现有两步调用和 Stage 0/1 需求准备路径仍通过相应测试。

## 2. 缩小包预检的明确误报

在 `src/shared/scripts/skill_preflight.py` 的实际文件集合检查中，只排除经过明确列名的无关普通文件，首项为 `.DS_Store`。排除规则只作用于额外文件；manifest 中列出的任何资源仍须存在，内容与 `0644` 权限仍须匹配，包摘要和固定真实根身份仍须验证。额外 `.py`、Skill 文档、其他未列名文件及符号链接继续阻断。不要把排除规则推广为按扩展名或隐藏文件通配。

`src/shared/scripts/host_skill_registry.py` 的加载错误继续阻断当前查询。现有错误可能没有可归属的 Skill 名称或路径，尚不能证明它与当前所需能力无关；若以后宿主提供可信的错误归属，再单独设计缩小范围的规则。

验收：加入 `.DS_Store` 后初次预检和已固定身份的 `verify` 均成功；修改、删除 manifest 资源，改变资源权限，加入未列名可执行文件或符号链接仍失败；注册表加载错误仍失败。

## 3. 收敛重复规则与措辞测试

逐条确定规则的权威来源：Stage 2 的方案就绪与变更契约规则由 `src/stages/solution-design/references/design-readiness.md` 定义；Stage 3 的执行/TDD 边界由 `src/shared/references/guided-implementation/execution-protocol.md` 定义；重复故障诊断由对应 originating-task 协议定义；角色和交接由 workflow-control 协议定义。阶段入口保留简短的执行指令、适用时机和必读引用。自包含包内保留生成的资源副本，不通过跨包链接共享运行资源。

调整 `tests/runtime/test_solution_design_contract.py` 和 `scripts/validate_repository.py` 中与这些重复句子相关的断言：一处详细规则检查其关键语义，入口检查引用可达和执行顺序；减少多个文档都必须包含同一组英文短语的断言。保留可解析的协议字段与交接模板、关键权限/禁止行为、包资源闭包和生成漂移检查。对旧措辞禁止名单逐项判断，保留仍会造成错误执行的项，移除仅限制无害表述的项。文字存在只能证明文档包含规则，不能替代实际行为测试。

验收：权威规则和入口指引各有明确位置；改写非关键措辞不会单独使测试失败；移除入口引用、颠倒关键执行顺序、破坏资源闭包或协议字段仍会失败。生成包与源保持一致。

## 实施顺序与完成条件

按主题更新、包预检、文档和测试三段实施，每段仅修改直接相关的开发源并运行聚焦测试。完成后重新生成 `skills/`，运行 `./scripts/validate.sh`，记录通过的检查及任何未覆盖的现场宿主验证。若某项需要改变已固定的包兼容键或恢复保证，先更新本方案并单独评估旧运行的影响。

本方案只定义改造及验收，不授权安装到本机、部署、发布或清理现有运行状态。
