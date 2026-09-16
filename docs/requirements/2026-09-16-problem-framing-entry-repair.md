# 1 拷问入口与专用任务写权限修复需求

状态：冻结需求快照，供 2 方案使用
来源：当前对话已确认的问题与用户“做吧”的方案授权
源码检查基线：7443e7d195f2b0c559f86413be643db86b3a2605

## 目标与授权

为最新版 workflow-pipeline 的 1 拷问入口及专用任务交接形成可实施方案，消除 Skill 指令与真实运行时状态转换的矛盾。当前授权仅限 2 方案的需求准备和规划，不包含实现、部署、远程发布或修改用户其他项目的安装状态。

## 已核实事实

1. problem-framing/SKILL.md 要求从已有 phase 0 话题通过 0→1 wrapper Phase Run 进入拷问；dedicated-grilling-protocol.md 同时要求在 current_phase=1 时准备 dedicated-stage 交接。handoffs.py 拒绝交接 stage 与话题 current_phase 不同的请求，而 phase_runs.py 仅在 finalize 时推进 current_phase。
2. 在临时项目通过真实 discussion_protocol.py CLI，从 phase 0 准备 dedicated-stage(stage=1)，返回 invalid_request：dedicated stage must match current requirements phase。
3. 专用拷问协议要求接受交接后立即开始提问并更新需求文档。真实 accept-handoff 返回 accepted-awaiting-next-turn，尚未授予文档写权限。
4. 在已处于 phase 1 的临时项目，通过 prepare-handoff → bind-handoff → accept-handoff 后执行 prepare-topic-update，返回 document_ownership_conflict；补充后续回合 authorize-handoff-discussion 后，同一更新成功。
5. 基线 bash scripts/validate.sh 共 335 项测试通过，仓库静态校验通过；这些结果未覆盖上述跨协议矛盾。

## 范围

- 从 phase 0 进入专用 1 拷问，以及已在 phase 1 时将拷问转交专用任务。
- 两条路径的控制身份、阶段运行/交接证据、需求文档唯一写权、接收和激活顺序。
- 门禁、取消、重试、重复交接、错误身份及不确定结果对上述路径的影响。
- 同步直接相关 Skill、协议说明和真实 CLI 行为测试，使可执行指令与运行时一致。

## 约束

- 复用现有 Discussion Topic、Phase Run、Conversation Binding、Workflow Controller 和需求文档机制；不为绕过入口校验提前伪造阶段完成。
- 同一需求文档始终只有一个有效写入者，错误或已失效的任务不能取得写权限。
- 跨阶段专用拷问必须满足来源授权和激活条件；门禁关闭不能开始实质工作。
- 普通拆分子话题原有“首回合接收、后续用户触发再工作”的行为保持不变；不得无差别放宽所有 handoff。
- 不新增后台轮询、状态注册表或通用调度框架，不扩大到模型选择、部署工具或外部子 Agent 治理插件。
- 不将中央技能安装版本或软链接同步问题混入本次源码修复方案。

## 方案待决事项

此前提出过候选方向：跨阶段使用 wrapper Phase Run，同阶段转交使用 dedicated-stage；专用交接接受后在满足权限和门禁时启动，普通子话题继续保留后续回合规则。这些是待真实调用链核对的设计候选，不是已冻结的具体实现。

设计阶段需确定唯一且完整的转换顺序、每步执行者、证据来源、持久化位置、错误与恢复行为、受影响文件及测试 seam。若发现必须改变上述范围或约束，应返回用户决策。

## 验收条件

- 从 phase 0 按修订后的 Skill 指令能创建并激活专用拷问任务，实际写入同一需求文档，无需提前标记 phase 1 完成。
- 已处于 phase 1 的合法专用转交能按规定步骤获得写权限，不因遗漏授权步骤卡住，也不多建阶段运行。
- 所有提问和文档更新都发生在授权生效之后；来源身份、任务、文档和阶段必须一致。
- 门禁关闭、取消或失效交接、错误身份和重复请求具有明确结果；不会获得越权、重复创建或丢失恢复位置。
- 普通子话题首回合仍不能开展实质工作，后续合法授权后可继续。
- 新增测试通过真实 CLI/ledger/临时项目覆盖上述路径，同时执行项目完整检查；不只添加文本关键词断言。
- 方案明确接口/模块职责、调用顺序、状态与失败分支、准确文件范围和验证依据，可直接交给实现阶段。

## 非目标

实现或部署本次修复；改造整个工作流；迁移旧部署数据；修复外部 subagent-governance 插件；修改其他项目。
