# 五阶段总控对话与执行边界

Status: ready-for-agent

需求权威：冻结的「总控对话与五阶段调度流程」草案，提交 `030ee5327eb0ccf20b9098dfe18dd67c9524b743`，SHA-256 `e5c1367ee0660b5975ad85d62e74f5b62fdd1b987183b8841b3157bbf95cd708`。D1–D10 是行为边界；本 Spec 不增加需求。方案模式：连续执行后续全部流程。

## Problem Statement

用户需要一个稳定的决策入口。目前专用任务和执行角色混用，0/1创建前准备与确认规则不同，拷问结果先归档后接管，3实现仍使用用户可见专用任务。总控归属可能漂移、确认重复、失败后的恢复位置不明确，且缺少按模块安全并行及按难度选择模型的共同规则。

## Solution

每个话题保留一个总控任务。0/1先整理唯一需求文档，再合并确认阶段进入与专用任务创建；用户选择当前任务时在同一文档继续。2派发方案子 Agent；3派发唯一实现调度子 Agent，由其按文件边界安排执行；4委托归档子 Agent。总控接受结果，在下一阶段已验证接管后才归档旧专用任务。沿用讨论协议、Flow Worktree、当前原生适配器和现有前台 runner，不建设后台接续平台。

## User Stories

1. 作为用户，我希望先看到整理好的唯一草案，再确认创建任务，以便批准具体结果。
2. 作为用户，我希望确认同时包括阶段、角色、模型与下一步，以免重复决定。
3. 作为用户，我希望本阶段拒绝新任务后直接继续，以免再次被询问。
4. 作为用户，我希望明确的话题级偏好持续生效，以免每阶段重新设置。
5. 作为子话题用户，我希望独立选择偏好，以免继承无关限制或连续授权。
6. 作为用户，我希望专用任务知道总控是谁，以免递归创建同阶段任务。
7. 作为总控，我希望拆题只创建一个子话题总控，并保留父级关联。
8. 作为用户，我希望推荐顺序和强依赖分开表达，以便知道必须等待什么。
9. 作为等待中的用户，我希望能接收材料和查看恢复条件，但不自动开展讨论。
10. 作为总控，我希望回传身份、文档版本和提交可验证，以免接收错误结果。
11. 作为用户，我希望新阶段接管后再归档旧任务，以便失败时仍能恢复。
12. 作为总控，我希望重试不重复创建、接收或执行，以免重复交付。
13. 作为用户，我希望成熟的0讨论可确认连续进入2、3、4，以免机械确认。
14. 作为用户，我希望简单改动可明确转为独立3实现，以免生成不必要规划。
15. 作为用户，我希望资格不足的请求仍进入1或2，以免遗漏必要决策。
16. 作为总控，我希望仅一个实现调度者统筹执行，以免争用工作区。
17. 作为执行 Agent，我希望有精确文件范围和交付要求，以便独立完成工作。
18. 作为用户，我希望共享文件和Git操作串行处理，以免互相覆盖。
19. 作为总控，我希望调度者中断后可恢复未完成部分，以免丢失已验收工作。
20. 作为用户，我希望统一集成、测试、审查通过后才算实现完成。
21. 作为总控，我希望归档者只收尾和发布，将实现问题返回3。
22. 作为用户，我希望重要角色披露模型和理由，普通执行只记录配置。
23. 作为用户，我希望能力失效时有限升级，成本或权限变化由我决定。
24. 作为用户，我希望前台runner和交互入口使用相同角色和完成规则。

## Implementation Decisions

### I1. 角色、接口与状态权威

采用 ADR-0007。新增 Workflow Controller（总控任务）、Dedicated Discussion Task、Dedicated Problem Framing Task、Implementation Dispatcher、Execution Agent、Closure Agent。Originating Task 是总控在3阶段的角色；Dedicated Implementation Task 不再是新3阶段的可见载体。保持 Discussion Topic、Phase Source Task、Phase Carrier、Conversation Binding、Flow Worktree 的现有含义。术语及旧角色文本在4归档更新。

总控身份由认证的任务/主机和可选的项目/tree/topic构成；每次入口区分总控、专用载体、原生子 Agent、scripted carrier。继承已验证总控，不能由标题、文本自称或当前执行者覆盖。子话题取得独立总控；普通阶段转换不创建总控；独立调用不创建讨论账本。

增加一个小的 Workflow Control 契约组件，供五个阶段及runner调用。输入为有界、版本化的控制上下文、动作、认证角色、冻结证据、明确用户意图与当前能力；输出为动作计划/更新检查点，或明确错误。它不调用任务工具、Git或存储独立日志。公开接口为stdin JSON CLI，runner调用同一模块；拒绝未知字段、非法阶段/角色、路径逃逸、身份不一致和缺少证据。计划不能替代真实工具证据或用户权限。

上下文包含 schema_version、controller_ref、topic_ref或null、stage、carrier(kind/ref/attempt)、preference(topic_current/stage_current)、flow_authority、requirement_identity和handoff_progress。总控是控制检查点唯一写入者。附着讨论的总控身份及偏好归现有ledger，active Conversation Binding仍归总控；独立0/1的文档写入者沿用草案metadata。其余交互执行用总控可信对话中的固定检查点；scripted执行保存在已有run_record，不增加第三份状态。Git仍是工作树事实权威；原生派发/终态仍由当前治理适配器负责。

### I2. 0/1准备与单一文档写权

0先bootstrap和唯一文档初始化，1先选已有唯一草案或按独立1创建一份。准备可写草案、checkpoint及组合确认计划，不能create_thread。计划冻结阶段、总控、目标、项目、标题、文档/hash、缺失上下文、模型/强度/理由、任务数1、下一步及拟归档旧任务。修改使计划失效；拒绝仅设置当前阶段偏好，明确话题级拒绝才设置topic_current；取消不推进阶段也不等于拒绝新任务。

优先级：已验证专用角色继续本阶段 > 话题级当前任务偏好 > 当前阶段拒绝 > 默认准备专用任务。偏好不限制原生子 Agent；阶段偏好不传下一阶段，子话题/独立调用重置。显式新选择可覆盖偏好，但重新冻结计划。拒绝创建仍沿用同一文档，无占位任务、无再次建议。

附着0/1在现有 Handoffs 增加 dedicated-stage kind，source_topic=target_topic，stage仅0或1；复用prepare/bind/accept与失败幂等状态，不产生结构关系，不创建第二个active Conversation Binding。记录独立carrier_ref和写权。阶段actor校验按operation allowlist授权read、当前问题/决定/叙述更新、DW apply、checkpoint和完成提交；禁止carrier创建子题、释放门禁、激活阶段、验收、归档、派发下一阶段。不能把通用owner检查改为所有carrier都是owner。carrier写需求时总控不能并发改写，但继续拥有只读、接收和门禁协调权。

独立1保留平台认证task-creation wrapper、write_owner转交与commit/hash交付。专用0/1不得递归创建同阶段任务，辅助Skills不触发创建。明确创建失败保留准备结果；不确定只按真实工具证据对账，不能盲重试；clientThreadId只表示pending，不作threadId。取消撤销attempt，迟到结果不获写权。

### I3. 子话题与门禁

保留checkpoint→prepare-handoff→create→bind→accept链，创建角色是child Workflow Controller，数量严格1。披露独立目标、边界、父级关联、推荐顺序及单独强依赖数组。首回合仅接收，后续用户触发才authorize。gate关闭允许接收/只读/恢复说明，禁止准备或创建专用载体、实质更新、推进0/1。重新关闭后在下一用户请求和入口重新检查，不轮询、不唤醒、不回滚已完成工作。Topic Dependency不参与3的Ticket与文件调度。

### I4. 接收、接管、归档与入口授权

恢复检查点记录prepared→creation-pending→carrier-bound→result-received→result-accepted→successor-ready→archive-pending→archived。当前任务执行跳过外部创建节点；无下一阶段停在result-accepted并保留旧任务。每个事实必须有证据。相同delivery ID/来源/版本/commit/hash重放只ack，新的版本不能覆盖已接管结果。

总控先验证认证来源、可信创建记录、项目/文档、版本和提交hash，再记录接受；准备下一阶段并合并披露确认阶段进入/必要创建/旧任务归档。接管必须证明真实carrier身份绑定、冻结输入一致、适用的worktree验证和角色ready；附着讨论还需claim→phase-ready→source activate。只派发成功或CLI退出不算接管。接管失败保留旧任务；成功后只归档冻结的旧可见任务。归档失败保留archive-pending并停止自动后续派发；已开始的工作仅可完成既定边界，不回滚、不重跑。恢复先查真实归档状态，已归档只补记，明确未归档才重试。所有异常/权限变化返回总控。

0→2与1→2使用共同连续授权：source_phase、最新已发布stage-entry CP身份/hash、topic/controller、明确披露stages=[2,3,4]和标准交接、scope digest、normalized continuous intent。0的Stage-1 result为null，1保留成功结果绑定。authorize-continuous-flow、wrapper prepare/ready/activate共用验证器，校验源阶段、最新CP、gate、pending impact与范围。拒绝旧CP、他话题授权、未披露阶段。PHASE_ROUTES不新增(0,3)，2后不查询Topic Gate。

0→3是显式独立路线：完整brief具有低复杂度目标、充分实现依据、允许路径、失败语义、验收和测试seam，并确认无需1/2；真实阻塞先处理。不携带Discussion Binding/Phase Run，不自动discover/rebind或改变原topic.current_phase；保留总控身份，来源讨论仅作背景。声称attached或失效2交接不得静默降级，须总控明确选择独立路线；资格不足返回1/2且不创建worktree/任务。

### I5. 单调度者、执行和恢复

总控只派一个原生Implementation Dispatcher，传Spec/Tickets或standalone brief、允许路径、测试依据、Flow Worktree、控制身份和配置。调度者验证cwd与binding后按blockers顺序工作，可自己串行执行；只有独立可验收且文件不重叠时才并行Execution Agents。每份执行envelope含真实agent ID、任务ID、精确相对文件列表、只读依赖、行为与测试要求；目录在派发前展开，新文件须列出。越界和重叠拒绝；共享文件由调度者串行编辑或其他执行者停写后重新分配。

Execution Agent不得改Git索引、提交、分支、merge、worktree或清理，不改规划源；只改分配文件并报告测试及问题。调度者是唯一集成/候选提交者；接收检查实际diff、范围和行为，所有执行者停写后统一集成、相关/全量测试并提交干净候选。测试副产物也需避免并发争用。总控独立执行Standards+Spec审查，调度/执行者不自审接受；修复回到同一调度者并保留既有diagnosis-first规则。

每次派发、接收、验收、候选提交返回总控检查点：执行身份/状态、分配范围、已验收结果/hash、未完成项、测试、candidate或null、binding。中断/容量不足时总控先证明旧调度及执行者已停写，再比对检查点/Git；只恢复未完成部分，可恢复原调度者、替换一个或由替换者串行执行。已验收bytes重新核对但不重做，未验收结果重新验证；无法证明停写不得替换。保持原范围，否则用户决定；成本/权限按I7。取消停止派发并协调终止写者，保留worktree及差异，不自动reset。

### I6. 归档委托与前台runner

总控派一个Closure Agent，传完整Stage-3 handoff、accepted candidate、review、实现/归档范围、protected paths及同一binding。它只做文档、发布、非force清理；实现缺陷或target advance需代码修复时返回总控→3。总控验证merge ancestry、changed paths、测试/review与候选一致、清理和剩余资源；partial cleanup不得报告完成，merge成功后只恢复清理不再merge。supervision四操作及Git锁不增调度状态。

runner继续拥有stage session、run_record、continue/needs_input/completed、中断/checkpoint。stage2 CLI carrier编排方案者，stage3 CLI carrier受总控委托派唯一原生dispatcher，stage4 CLI carrier派Closure Agent并验收；最终用户决策返回root。更新角色prompt及handoff校验，携带controller_ref/control checkpoint；保留外层result schema语义和same-session resume。完成证据须绑定角色身份、候选及review，不只非空；runner不重复Git发布或语义审查。scripted carrier返回本阶段handoff后停止，由runner唯一推进下一阶段；交互入口才同回合连续调用。technical_error仍是恢复错误，不伪装权限问题。

### I7. 动态模型与强度

Workflow Control的纯选择接口接收角色、复杂度/风险、当前工具支持组合、用户指定/已冻结配置、receipt和成本边界；返回具体model/effort、reason/source、能力证据及needs-decision。settings resolver继续仅可靠读取/verify，不改v5存储解析，也不读取更多私人消息。

优先级为受支持的用户指定 > 仍受支持的已确认配置 > 按任务选择。只用当前目标工具公开的组合和能力描述、已知成本，不硬编码型号/价格；难度高的方案、调度和冲突归档使用足够能力，窄范围执行可以更经济，不能将所有执行视作简单。无法比较能力/成本时保留已有受支持配置，不宣称未知成本更低。重要角色和可见任务启动前披露具体配置及理由，普通执行只记录。适配器不能覆盖时只能记录实际可表达的继承配置，不能伪造选型。

能力失效只计算一次升级候选并重新验证，不循环试型号。无可用组合明确阻塞；成本增加或未知、权限变化、可见身份变化均需要总控新确认。只有可证明不改变边界的升级可自动记录后执行。用户指定不可用不能静默替代。串行降级不绕过这些规则。五阶段模板、实际create/native dispatch及runner使用同一选择结果。

### I8. 变更契约预检与真实调用链

| 已读取生产链及原行为 | 矛盾、拥有者与解决方式 |
| --- | --- |
| bootstrap→registry→RequestContext→ledger；prepare-handoff→bind→accept→authorize | continuation替换active binding，尚无专用0；I1/I2在Handoffs加入dedicated-stage，保留总控，operation allowlist限制carrier；DW/attempt失败语义沿用 |
| dedicated-grilling→create_thread→trusted checkpoint→commit/hash delivery→intake | 现文先archive再enter2，与D6相反；I4先接收、接管再archive，控制转换器校验顺序，外部工具提供真实回执 |
| child handoff→authorize-handoff-discussion→apply_gate_policy | 已有首回合例外与gate；I3角色改子总控，专用创建同样gate检查，保留AND及一跳失效 |
| authorize-continuous-flow→verify_stage_one_checkpoint→wrapper→phase-ready→activate | 当前代码/测试拒绝phase0连续；I4共用0/1验证器，0用当前CP和null stage1 result，不新增(0,3) |
| guided→Originating Task→dedicated executor→candidate→two-axis review | 当前是可见执行任务；I5改唯一原生调度者，可派执行但不派审查，Git单写者，总控接受 |
| workflow start/resume→_advance→_invoke→_stage_prompt→codex exec→result/continuity | stage3旧角色、stage4称CLI自己executor；I6仅更新角色payload和控制证据，保留session/runner推进 |
| change-closure→complete-worktree→merge→remove worktree/branch | 当前源任务执行归档；I6委托并验收，保留publication/cleanup不同失败语义 |
| settings resolve/verify→indexed rollout→receipt→launch | 当前模板固定继承；I7在receipt之上选择，能力由实际适配器输入，不改resolver私有解析 |

均为D1–D10范围内冲突。保留ADR-0001讨论/Git分离、ADR-0005独立入口、ADR-0006门禁。D10无旧交接，新控制schema拒绝缺失身份，不新增迁移/重绑/旧角色兼容；无关既有schema兼容不清理。

## Testing Decisions

用最高可观察seam：真实讨论JSON CLI/registry/ledger/临时Git、Workflow Control CLI与真实调用方、现有runner伪外部codex进程。首个TDD行为必须经过真实生产调用边界；不得mock被改owner/转换/handoff验证，只替换外部任务工具、native适配器、codex进程并记录调用trace。Skill文本不能执行工具，静态检查仅补充，不声称真实桌面端到端。

| ID | 覆盖验收/分支 | seam与级别 |
| --- | --- | --- |
| T1 | prepare无create、确认1次、阶段/话题拒绝、修改/取消、非递归及偏好不跨子题 | Control CLI/阶段调用与外部trace |
| T2 | 总控binding唯一、单一DW写权、carrier不能split/activate/验收、迟到失败无权 | discussion CLI真实ledger/DW/checkpoint集成 |
| T3 | 子题仅1总控、首回合接收、closed gate不创建/实质写/推进，用户触发恢复 | 现有topic dependency CLI fixtures |
| T4 | 身份/版本/commit/hash错误、重复回传、accept→ready/activate→archive、每步失败及未知效果 | Control转换+真实Git hash调用方+外部trace |
| T5 | 0/1连续绑定CP/范围，drift/impact/gate拒绝，子题无继承，合格0→3无Phase Run | discussion CLI及guided入口集成 |
| T6 | 仅1dispatcher、非重叠并行、共享串行、越界拒绝、禁止执行者Git提交、统一候选 | Control分配/验收生产边界+临时Git+适配trace |
| T7 | 旧写者未停不替换、已验收不重做、未验收重验、串行降级/取消保留 | 同seam检查点恢复与Git范围比较 |
| T8 | closure只收已审候选、代码问题返3、cleanup失败不重复merge、partial不完成 | supervision临时Git+closure控制结果 |
| T9 | 用户优先、角色选择、有限升级、未知成本需决定、普通执行仅记录、拒绝unsupported | 选择器行为+CLI/runner真实消费者 |
| T10 | scripted各stage一次推进、continue同session、needs_input暂停、坏handoff拒绝、总控不漂移 | 现有workflow fake-codex进程测试 |
| T11 | 五Skill/refs/templates/glossary/schema/validator一致，无旧归档/可见实现规则 | contract tests + repository全量validate |

Readiness通过：I1–I7均有owner、输入输出、失败、seam；I2/I4/I5有状态与恢复；T1–T11覆盖冻结验收和重要分支。prior art为既有discussion handoff/continuous/gate、supervision临时仓库、runner session/duplicate/structured handoff及solution-design契约测试。实现先相关红绿，再全量validate；不需真实创建用户任务、付费调用或远端写入。

## Out of Scope

后台监控/轮询/唤醒、通用调度服务、新runner/注册表、旧交接迁移、冻结需求修改、多实现调度者、Gate扩到2–4、附着(0,3)、辅助Skill建任务、硬编码型号/价格、远端发布/PR/部署/安装及额外付费授权。

## Further Notes

原生 $ask-matt 判断需要Tickets：跨五阶段状态/执行责任需要多上下文。原生 $to-tickets 采用5个完整行为切片，01→02→03→04→05；最后模型切片依赖所有真实角色入口。连续授权覆盖测试seam和粒度选择，不增加review。

证据与实现定位（仅改本Spec直接需要的文件）：

- [冻结需求](../../docs/problem-framing/2026-09-09-workflow-control-conversations.md)，只读保护。
- [讨论Skill](../../skills/design-discussion/SKILL.md)、[拷问Skill](../../skills/problem-framing/SKILL.md)及其references/templates；[discussion_protocol.py](../../skills/design-discussion/scripts/discussion_protocol.py)、discussion_core的handoffs/phase_runs/state/registry及受影响actor/gate调用方；同目录相关测试。
- [guided Skill](../../skills/guided-implementation/SKILL.md)及originating/execution/settings/worktree协议；新增其scripts/workflow_control.py、必要私有模块及test_workflow_control.py。
- [workflow.py](../../skills/guided-implementation/scripts/workflow.py)、test_workflow.py、必要的workflow_stage_result.schema.json；settings resolver仅必要接线，不改v5读取语义。
- solution-design/change-closure的SKILL、references及contract tests；必要新测试遵循既有scripts布局。
- [validate_repository.py](../../scripts/validate_repository.py)、必要validate.sh/check-dependencies.sh仅更新契约与测试发现；不改外部治理包或安装配置。
- 4归档文档：CONTEXT.md、README及相关docs五阶段说明、以上Skills/refs、此Spec/Tickets生命周期。ADR-0007在2已提交。

3阶段遵守当前“实现提交不含文档”：完成代码/测试，交付逐文件待归档文档及具体替换要求；4更新Skill/模板和术语后再次验证并发布。不能漏掉文档，也不能删测试隐藏差异。对必须与新文档同时验证的contract tests，3可在临时验证副本应用精确待归档文本验证，须明确该结果不等于候选已包含文档；当前候选既有全量检查仍须通过。最终4完成文本后运行新契约及全量检查。
