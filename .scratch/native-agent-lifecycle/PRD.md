# 原生子 Agent 生命周期与连续执行安全续接

Status: ready-for-agent
Lifecycle: completed

## Problem Statement

连续流程把 carrier 一轮的 `continue` 当成可以退出并重新启动宿主的信号。正在执行的原生角色因此可能失联；复用会话 ID 和有效 JSON 都不能证明原角色仍存活。用户需要正常进度无需人工恢复，异常也不能重复派发、覆盖工作或重复发布。

冻结需求：`docs/requirements/2026-09-20-native-agent-lifecycle.md`，commit `906efc37037c11d4a8b5ee99eac301079368fb8e`，SHA-256 `b14ba8d027b63eb7de5ae283173e9ed46b842a7f13c01f753fba6fade64c9906`。本 Spec 不修改需求，沿用 CONTEXT 的 Workflow Controller、Implementation Dispatcher、Execution Agent、Closure Agent 和 Flow Worktree。

## Solution

让已有前台 runner 在本次运行内部持有一个前台 app-server，跨 carrier 轮次保留宿主。轮次事件与宿主退出分开处理；running 表示在同一宿主等待，只有原身份当前可继续且 C 允许时才发后续工作。本机隔离实验已证明该方式支持子 Agent 跨父轮完成和同身份 followup；跨宿主崩溃恢复未获证明，按明确阻塞设计。

暂停和等待用户输入继续占用当前前台运行；用户仍用既有 pause/resume/cancel 命令，命令向原 checkpoint 提交精确控制请求，由唯一 owner 消费。取消先收集原生执行树的停止证据再释放宿主。崩溃保留既有现场，未获得原身份和未解决调用的真实对账前不重派。

## User Stories

1. 作为用户，我希望原生角色运行时只输出进度，从而不会因正常等待失去执行者。
2. 作为用户，我希望 carrier 下一轮仍能访问同一角色，从而保留工作范围和上下文。
3. 作为用户，我希望子角色本轮完成与整个阶段完成有明确区分，从而不会漏做审查或发布。
4. 作为用户，我希望等待问题答案时保存宿主，从而回答后无需重新派发。
5. 作为用户，我希望暂停停止所有当前写入者，从而能安全检查现场。
6. 作为用户，我希望暂停后明确恢复只继续原授权工作，从而不会恢复旧的取消动作。
7. 作为用户，我希望取消覆盖嵌套执行者和独立审查者，从而没有遗漏的后台活动。
8. 作为用户，我希望崩溃后的文件和提交保留，从而有可核实的恢复依据。
9. 作为用户，我希望丢失回执不会重放写入，从而避免重复派发和重复发布。
10. 作为用户，我希望重复提交答案幂等，从而不产生第二次工作。
11. 作为用户，我希望相互冲突或过时的答案被拒绝，从而决策不落到错误问题上。
12. 作为用户，我希望找不到原角色时明确阻塞，从而不会用新人冒充恢复。
13. 作为维护者，我希望生命周期证据仍归原 checkpoint 管理，从而不出现第二套状态权威。
14. 作为维护者，我希望真实宿主验收与模拟测试分开，从而不会高估宿主支持能力。
15. 作为维护者，我希望旧运行继续绑定原包，从而升级不破坏已有现场。
16. 作为维护者，我希望阶段 2、3、4 使用同一生命周期语义，从而不在归档或审查时重现问题。

## Implementation Decisions

### D1 — 生产调用链与变更契约预检

证据定位集中列在 Further Notes。以下是当前真实入口、调用者、所有者及矛盾修复决定。

| 入口与生产调用链 | 所有者与边界 | 状态、顺序及矛盾处理 |
| --- | --- | --- |
| runner start/resume → advance → invoke → process loop | runner 仅管理宿主和 carrier 传输；输入冻结确认对象，输出进度、精确问题或 C 投影结果 | 当前每轮 exec/resume 退出后才读结果，continue 无 running 门禁；改为同一前台宿主内处理 turn 事件，宿主结束必须经过 D4 屏障 |
| carrier prompt → C start/advance → B prepare/bind/receive/accept | C 管业务状态、action_id、原身份、控制权；B 保持手交接及原生角色验收 | next_action 是唯一允许动作，不能由外层 continue 文案自行派发；CLI/carrier 身份始终不是 native ref |
| C continuation_gate → query_host → observe/record_host → effect | C 保留业务意图、generation、provenance、未解决调用；宿主适配器提供真实调用证据 | running 只能 wait；idle/turn-completed 加当前因果证明、原范围授权和已解决 action 才能继续；stopped 仅证明停止，不证明可恢复 |
| pause/cancel command → runner_request → C suspend/advance_stop → control effects | 原 checkpoint 保存请求；C 统一停止优先级；宿主仍可用于查停原角色 | 当前 cancel 立即杀本地进程会丢失执行停止操作的通道；改成先原生查停及嵌套闭合，后释放宿主；紧急进程清理不伪造 native stopped |
| dispatcher allocation → B/control executions roster → receive/accept | 原 control executions 保持文件分配与写入者权威；C allocation journal 保持调用证据 | 根角色停止不能代替所有 execution 的停止；未绑定派发必须对账 actual not-created 或真实绑定后停止 |
| Originating Task → 独立 Standards/Spec 审查 → candidate acceptance → closure | 原角色职责不变；审查 ref 与候选固定点关联 | 审查角色也进入宿主停止屏障，不能仅根据 dispatcher 或 C 单一 host 状态释放宿主 |
| publication readiness → C publication/reconcile/receive-publication → D Git primitive | Git、C/B 接收链保持完成权威 | transport turn 完成不等于 native 停止、B 接收或 Git 发布；丢回执只对账原 publication，绝不从头重跑 |

### D2 — 所有权与接口

runner 新增一个实现局部的前台宿主适配模块，负责启动、initialize、请求关联、通知读取、精确 thread/turn 定位和有界关闭；不接管 Workflow Controller、原生派发治理、Spec/Tickets、Git 或 C 的推进决策。由 runner 注入传输 seam，测试可替换字节流/进程，业务测试仍走真实 runner 和 C。只暴露启动当前 run 宿主、开始/继续 carrier turn、读取事件、请求停止、结束宿主几个操作；不提供任意 shell 或另建任务的通用代理。

选择本机 app-server stdio，理由及真实宿主回执见 Further Notes。初始化与 thread/turn 请求按当前本机导出 schema 构造。model、reasoning effort、项目 cwd、worktree 附加写范围、sandbox 与审批策略必须保留已确认值，unsupported 字段/能力在首次业务写入前停止，不能静默扩大权限或换模型。

本机 schema 已确认 thread/start 的 cwd/model/config/runtimeWorkspaceRoots/approvalPolicy/permissions 或 sandbox、turn/start 的 threadId/input/effort/outputSchema，以及 turn/steer 的 threadId/expectedTurnId/input。线程与轮次参数使用 schema 的真实拼写；model fallback 必须关闭或验证等价冻结结果，不能靠默认模型漂移。permissions 与 sandbox 不混传。thread/read 的历史数据及 notLoaded 不能当 current live proof。初始 sandbox/approval 不硬编码成 danger-full-access 或 read-only，继承当前确认配置并验收 worktree/Git 和必要治理记账权限。新 version-4 confirmed input 在原配置内冻结 host transport=app-server-stdio、实际 CLI 版本和完整有效执行权限配置及其可信 Controller 来源；模型/effort 仍由各 stage 已有设置拥有。缺少实际配置来源不能猜测默认值。运行时回读实际 thread 配置并比对，不匹配在首次业务派发前停止。

宿主适配器严格区分 RPC result、error、notification、server request 与模型消息。request_id 只在本连接命名空间有效，turn_id 与 thread_id 必须匹配；乱序通知不能接收成当前轮结果。原生生命周期通知按其原始 thread/turn/ref 单独归因：真实实验显示子完成事件可能在下一父轮期间到达、仍引用上一父 turn，因此不能全局丢弃旧 turn 通知；它可结算原调用，却不能完成当前 carrier turn。subAgentActivity 的 kind=started 且 item/completed 只表示派发工具条目结束，不是 child completed；需 kind=completed 加真实当前状态/原生终态回执，不能混淆。commentary/delta/progress 只输出进度。仅匹配当前 turn 的终态和完整结构化结果可进入既有 C/B 验收；原生角色结束消息仍由其原生父角色接收。宿主 server request 保留请求 ID、方法、原参数和 host instance，仅能以对应 Controller 决策回答；丢失连接后不能在新实例重放批准。未知审批/外部副作用请求不自动批准，回交已有 Controller 决策通道。

### D3 — 同一 checkpoint 与并发命令

保留 runner 的整次运行 RunLock；唯一持锁进程拥有当前 app-server 和 carrier 连接。短 checkpoint lock 仅用于事务读改写；任何等待、RPC 和原生工作都在锁外。C 的三个现有成员保持其写入所有权，runner 继续合并保存，不覆盖 C 的新 revision。

在原 runner 外层记录中增加 transport 生命周期字段：本次 host instance nonce、adapter/protocol 版本、进程观察信息、carrier thread/turn、正在进行的 transport request、关闭状态和证据引用。它们只描述传输，不建立可写权限、原生 ref 或第二套执行账本。transport request 在发出前保存意图，原始响应按同一记录、instance、请求、thread/turn 保存于现有 carrier receipt 位置，保存失败或未知发送结果不重发；通知可追加，消费幂等。

活跃 owner 期间，resume 不等待获取整次运行锁，也不启动 executor：先在短锁内校验记录版本、同一 pending decision ID/subject、答案、停止优先级、current package 注册证据，再排队到原有 answers/controller_decision/runner_request 的相应字段。返回 queued 表示已保存，绝不表示已执行。重复同一答案 ACK；不同答案 decision_conflict；过期 ID stale_decision。用户答案只有在原 carrier context 通过 C decide 才算消费。暂停期间答案按既有 deferred_decisions 保存，cancel 优先且不被 resume 答案解除。

queued resume 请求也必须区分普通答案和明确解除暂停：裸答案不解除暂停；显式 resume 意图记录原 stop request/当前 C subject，由 live owner 在同一 carrier 应用 C resume，然后消费 deferred 答案。cancelled 仍只能现有 Controller 恢复，不受这个通道解除。

控制命令先尝试非阻塞 RunLock：能取得锁意味着没有 owner，应走 D6 恢复；不能取得锁只允许提交请求，不能凭持锁推断宿主健康。请求与 live owner 同时消失时，保存的命令仍留待对账；不能因 queued 无回执重复 launch。owner 正常释放前在短锁内冻结关闭状态并处理已提交请求，后来的请求进入明确终态/恢复路径。PID、时间戳、锁和 instance nonce 是定位资料，不是原生活性证明。

### D4 — 退出前保护和嵌套停止屏障

宿主存续覆盖当前 run 的所有 carrier turns，包括等待子角色、独立审查、needs_input 和暂停；阶段边界可保持同一个宿主。轮次完成后不关闭 stdin、不等待 app-server 进程退出、不归档 carrier。正常最终释放仅发生于最终 C/B/D 完成，或所有当前原生工作已证实停止且没有未解决派发/继续/发布动作的显式取消。暂停和 needs_input 保持前台占用，展示原 checkpoint 与 pending ID，等待用户通过另一命令回答或控制；没有后台 daemon、开机恢复或 scheduler。

等待以不超过 60 秒的有界读取/原生等待片段执行，持续读取通知和原 checkpoint 控制请求；单次超时只返回进度，不判死、不结束宿主、不自动重派。稳定的 running 等待不计入既有“相同 continue 无业务进展”计数；真正重复的业务 continuation 仍保留原 no_progress 规则。

停止目标从既有 dispatch root、C allocations / control executions 的实际绑定和当前候选的 Standards/Spec reviewer refs 推导，不新增通用子树 registry。C 的 observations 继续保存原始因果回执；投影可丢弃重算，不是权限或角色分配权威。为覆盖尚未交付 review result 的窗口，在原 C checkpoint 中仅增加当前候选的两个固定 review_activity 槽（standards/spec）：各保存原候选、原 Originating Task、启动意图、原始调用回执和机械返回 ref。候选改变需保留旧回执，完成停止后才可开启新的固定点；不允许任意角色注册。

review-activity 是 C 中一个有界接口，仅 Stage 3 原 Originating Task 可提交 prepare/observe，axis 只能 standards/spec，候选必须是当前已验证 candidate。prepare 留下唯一意图和 action_id 后，原 Originating Task 按已有独立审查职责调用原生适配器一次；observe 接收该调用的原始回执/provenance，绑定机械 ref。重复输入 ACK，冲突阻塞，未知创建保留 exact lookup；这个接口不改外部 code-review 或治理 Skill，不分配实现路径或扩展审查权限。其余角色继续走现有 B/C 原生 dispatch/allocation 接口，不再登记一份。

停止屏障是以上既有目标、未解决调用与宿主事件的派生结果。原生工具通知可证实实际 parent/ref 边以及出现了未对账派发；不能据通知、标题或唯一候选补建业务角色。运行中事件使对应旧停止证明失效。未知额外后代会关闭屏障，并交回其实际原生父角色的现有治理链查停；不是自动纳管新角色。根与已知子角色都停止，仍需原宿主提供父调用终态和无未解决派发的闭合证据；缺失就阻塞。真实适配器若支持精确祖先枚举，只用于检查已绑定范围没有未对账成员，不用它重建身份或接受模型自报 tree_closed。

停止先撤销新增/继续授权，要求活跃父角色完成其已知子角色停止/对账，再收集根、Execution Agents、审查者及其已知后代的停止事实。原生父角色无法响应时，只能使用真实支持的 exact-ref 控制；不得跳过身份绑定或伪造父 actor。闭合条件是全部实际回执 ref 无活跃 turn，所有未解决创建/继续调用已分别证明 terminal outcome，且不再有可创建后代的未结束调用。单一 root idle、process group 退出、候选提交或缓存 stopped 均不满足。

### D5 — 分支优先级与正常续接

优先级：取消 > 暂停/停止屏障 > 未解决调用/传输对账 > 业务阻塞 > 用户决策 > 正常 C next_action。已接受的完成数据保留，不能用新请求撤销历史发布；只有未完成副作用被停止门禁拦截。

| 观察和意图 | 允许动作 | 禁止推断 |
| --- | --- | --- |
| carrier 仍运行、child running | 保持同一宿主，继续收通知；控制请求走有支持的 turn steering 或下一可处理点 | 不结束进程、不发送重复 user turn |
| carrier turn 完成、child running、C wait-host | 原 carrier 同一 thread 下一轮仅处理原 wait-host；必要时 native wait，不能再 invoke | continue 文案不授权新的角色 |
| child 当前 turn-completed/idle、C 有业务 continuation 且 action 已对账 | 通过 C 的原身份 gate 后 followup 同一 ref，记录新调用一次 | 完成历史或 creation ready 不等于当前可续 |
| needs_input | 保存精确 pending，保宿主，展示问题；queued 答案由同一 carrier 消费 | 用户答案不证明 child 已停止或可恢复 |
| pause | 冻结派发，收集完整停止屏障；之后标记 paused 并保持宿主 | turn.completed 不等于 paused |
| cancel | 升级停止意图，处理嵌套树；已闭合才 cancelled/关闭宿主 | 进程 kill 不等于 native stop |
| native unknown/缺失/不支持查找 | 保存原 ref、raw、action；await-host-recovery，停止自动查询/推进 | 不按标题/唯一候选恢复，不自动替代 |
| technical_error/no_progress | 原 business_block 保持，等待原 Controller recover-business | 新活性事实不能解除业务阻塞 |
| completed | 只消费 C 的 completion.stage_result 和现有全链完成验证 | 不用模型 footer 或 transport success 替代 |

### D6 — 退出后恢复与安全边界

app-server EOF、协议错误、owner SIGTERM/崩溃、未确认 RPC 写入或回执持久化中断，均先记录 transport-lost/uncertain 并撤销当前可继续证明；不改变已保存的 B 接收、Git 事实和原生身份。关闭本地进程组是资源清理而非 native stopped；如果原生状态未知，保留 pausing/cancelling/recovery-required，不能输出 cancelled/completed。

无 owner 的 resume 先恢复确切 transport receipts，再走现有 C 的原交易/B/发布对账。已完成 turn 可摄入，不启动那一轮。只有宿主提供真实、可认证的原 thread 和所有所需 native refs 当前查找与原 unresolved_action 对账，才允许 attach 原身份；没有这样的能力则 fail closed，报告原 checkpoint、原 ref、未完成动作与受限能力，交回原 Controller 已有恢复流程。本次不承诺跨宿主崩溃恢复，不自动重建树、fork/replace 角色、迁移旧 runner 或写入生产项目。

可证明 host request 未发出且无原生角色的 prelaunch 继续沿现有非创建恢复；已完成 run 重复查询 ACK；已发布 merge 只走 cleanup/reconcile，不能重新 publication。失联角色和不完整停止证明不允许删除现场、关闭恢复记录或清理 Flow Worktree。

### D7 — 兼容与生成包

新 runner 记录提升为 version 4，C 协议提升为 workflow-progress-v6；新增 transport 标识只用于这种记录，现有 B handoff 与 Flow Worktree 协议如未改形状保持版本。所有五个生成包的兼容键同步 C 新版本；stage 2/3/4 模板、载体提示、共享 progression/control 文档使用同一语义。runner 和候选包固定实际根与 digest，current registry 验证继续以任务实际项目 cwd 查询，工作命令在 Flow Worktree 执行，不用工作目录替换项目注册上下文。

旧 version 1/2/3 或 C v1–v5 保持只读拒绝并返回 legacy_run_requires_original_runtime，要求原包恢复；不自动升级记录、替换 pin、改安装或迁移原下载任务。生成包从共享源构建，禁止只补已安装副本。真实 CLI schema/host 能力不可用时新运行在派发前阻塞，不能降级回 exec/resume。

## Testing Decisions

采用最高可用 seam：runner 公共 start/resume/pause/cancel 与真实 C/B/checkpoint/Git fixture 联动，只有宿主字节传输替换。先用能复现 continue+running 导致第二个宿主的行为测试变红，再推进实现；不以字段字符串存在或 mock _advance 输出冒充修复。

| 验收 | 测试与可观察断言 |
| --- | --- |
| A1 活跃角色等待不退出 | 同一 fake app-server 多 turn 运行真实 runner，记录 spawn/EOF 数量；carrier continue+真实 C running 后实例数仍 1，仍可处理 native 完成事件；进度通知不能生成 stage_result |
| A2 同身份正常续接 | 精确 ref、action_id、generation、调用次数贯穿 C；running 不续发，idle+旧 proof 不续发，新因果 proof 加业务意图只 followup 一次；原生真实实验另证身份 |
| A3 崩溃/丢回执无重复副作用 | 在 request 保存前后、写 pipe 后、原始回执保存后、主 checkpoint 忙/保存前注入中断；重新 resume 只摄入/对账，spawn、发布计数不增，文件哈希和原未解决调用保留 |
| A4 暂停取消嵌套闭合 | dispatcher+2 executions+2 reviewers，父先停、子后停、未绑定派发、迟到 ref、unsupported lookup、取消覆盖暂停；任何缺证据均不能 paused/cancelled/正常关宿主 |
| A5 真正调用链与宿主分离 | 使用生产公共命令和 C.handle；独立真实宿主实验保存原始 RPC/原生回执，验证单层跨 turn running、followup；真实嵌套停止单列外部依赖待验证；模拟通过不生成 host pass |
| A6 完整失败与兼容 | 协议错误、RPC ID 冲突、乱序旧 turn、包切换/缺失、旧记录、原生查找未知、业务阻塞、partial cleanup 都返回对应恢复分支，禁止新 dispatch |
| 答案并发 | 活跃 owner 的并发 resume 相同答案 ACK、冲突答案拒绝；pause/cancel 与答案竞争按停止优先；queued 后 owner 死亡保留答案不 launch；完成关闭竞态可恢复且不双消费 |
| 权限与资源 | 冻结模型/effort/cwd/写范围不漂移；未支持参数派发前停止；所有等待片段有界；正常闭合与异常资源清理不遗留本次本地进程，清理回执不充当 native 停止 |
| 全流程与发布 | 临时仓库运行 stage 2→3→4，B 接受、审查 fixed point、规划发布、最终 merge/cleanup 各一次；中途重复 completed/resume 不重复执行 |

既有 prior art 是 runner CLI 进程 fixture、C workflow progress 的因果回执与事务恢复测试、publication progress 的真实 Git fixture、control Git 的分配/候选审查检查。新增 fake app-server 只模拟传输协议，使用严格记录实际请求次序的状态机，不伪称宿主能力。完整项目检查包含确定性 build/check、运行时测试及 repository validator，并必须运行项目要求的 `./scripts/validate.sh`。真实宿主验收必须在隔离临时目录，保留版本、配置、原始事件、精确 ref、进程退出/清理结果；不能对生产下载任务执行 resume。

### 方案就绪与切片决定

方案就绪通过；根据重新冻结的需求，外部嵌套集成单列待验证，不阻断本仓交付，运行时门禁不变。已完成：D1 的每个入口沿生产 caller 查到所属模块，D2/D3 定义接口和持久化所有者，D4/D5 覆盖停止、恢复与信号优先级，D6/D7 明确失联和兼容分支，A1–A6 逐项映射直接可观察 seam。连续模式下原生 to-spec 测试 seam 问题选择“公共 runner + 真实 C/B/Git，宿主 transport 替身；真实宿主另验”，无需额外用户审阅。设计就绪不表示产品实现或全部真实宿主验收已完成。

ask-matt 原生路由判定：这是多上下文构建，应该用 to-tickets 分解。选择四个纵向切片：T1 同一前台宿主正常推进（无依赖）；T2 活跃 owner 的用户决策与完整停止（依赖 T1）；T3 宿主丢失与回执恢复（依赖 T1）；T4 全流程、生成包及真实宿主验收（依赖 T2、T3）。T2/T3 可独立验收但共享 runner/C 的修改必须串行集成，不因此放宽文件分配。没有必要增加单独宽重构。连续模式预授权下，to-tickets quiz 的粒度与阻塞关系按此选择，无待用户决定的产品或架构分支。

## Out of Scope

常驻 daemon、远程服务、后台监视器、第二套状态账本、改写 Workflow Controller 或阶段角色、外部 Matt 或治理插件改动、安装/部署、生产项目写入、原下载任务重跑、自动迁移旧运行、自动替换失联角色、扩大权限。跨宿主恢复若当前 API 无法证明，就明确阻塞，不在实现期临时创造替代架构。

## Further Notes

### 调查依据

- 源码基线与实际调用链：`src/stages/guided-implementation/scripts/workflow.py` 的 `_stage_prompt`、`_run_process`、`_invoke`、`_advance`、`resume`、`request_control`；`src/shared/scripts/workflow_progress.py` 的 `advance`、`effect`、`continuation_gate`、`record_host`、`query_host`、`advance_stop`、`allocation`、`resume`；`stage_dispatch.py` 的 `receipt_for`/`receive`；`workflow_control.py` 的 executions、review 和 cancel。
- 权威协议：`src/shared/references/workflow-progression.md`；`src/shared/references/guided-implementation/workflow-control-protocol.md`、`originating-task-protocol.md`、`execution-protocol.md`；ADR-0007 角色分离与 ADR-0008 固定自包含包继续生效。
- 测试定位：`tests/runtime/test_workflow.py`、`test_workflow_progress.py`、`test_publication_progress.py`、`test_workflow_control_git.py`、`test_attached_flow_progress.py`。
- 本机 `codex --version` 为 `codex-cli 0.155.0-alpha.9.2`；`codex app-server --help` 提供 stdio 前台入口。官方文档确认连接初始化、thread/turn API 与流式通知，未独立承诺本次嵌套原生角色生存语义：[Codex App Server](https://learn.chatgpt.com/docs/app-server?translationFallback=zh-Hans)。
- 已核实 task cwd 是主项目，命令 workdir 是 Flow Worktree；宿主 skills/list 在前者有效，二者不能混淆。

### 真实宿主能力证据

2026-09-20，主 Agent 在仅用于实验的临时工作目录启动一个前台 app-server，原任务及生产项目无写入。第一轮父 carrier 派发一个等待 45 秒的原生 child 并立即 final；第二轮保持同一宿主 PID，查询原 child、等待其完成、对同一 identity 发一次 followup 并等待完成。实验不是本仓库 runner 实现后的端到端验收。

- 原始目录：`/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/host-probe-authorized/`；`report.json` 与 `events.jsonl` 已只读核对。
- 父 thread `01a0bf28-d2b9-7403-a806-b24e9a87a588`，PID `46553`，第一 turn `01a0bf28-d334-7f43-83aa-916b45a0914c` 于 `1789913575.426655` 完成；第二 turn `01a0bf29-b004-7763-ba1e-d7eb75d7e282` 于 `1789913651.987581` 完成，报告 `same_process_alive=true`。
- 原生回执映射：canonical ref `/root/sg_standard_wait_45_seconds_using_a_bounded_tool_t_b984ecaf4845`，agentThreadId `01a0bf29-7d20-79b0-8750-61aa6f2bd3ed`。原始通知记录 started `1789913562.479517`、completed `1789913620.075679`、interacted `1789913631.0673778`、第二次 completed `1789913635.075306`。两次结果分别为 `LIFECYCLE_CHILD_DONE` 与 `LIFECYCLE_FOLLOWUP_DONE`，没有替代派发。主 Agent 报告本次宿主最终退出码 0。
- `events.jsonl` SHA-256：`cb88a899ae470ac59815d80e4d1c340971b5e68c529ae7362e96b755a1037e24`。首次第二轮 query=running 是实验 carrier 的报告；客观最低证明是父轮已结束后原 child 的真实 completed/interacted 事件及同 PID 跨轮持续。
- 先前 read-only 实验被必要治理记账权限拒绝，没有 child；不计为生命周期失败或成功。重试沿主任务已有 danger-full-access 权限仅授权隔离实验及必要治理记账；产品必须继承实际权限，不能照抄该实验值。
- 本机 schema 的 ThreadListParams 提供 ancestorThreadId/parentThreadId，ThreadRead 提供 includeTurns；这些接口的存在不是嵌套树停止、跨进程恢复或本产品实现通过的证据。嵌套真实集成因外部治理依赖单列待验证，不计为通过且不阻断本仓交付；本仓生产 seam 的嵌套确定性验证、单层真实生命周期、暂停/取消产品适配链和配置迁移仍在 T2/T4 必须验证。生产运行缺证据始终按 D4/D6 阻塞，不留待实现另选架构。

单次 exec 持续不 final 可缓解一轮内等待，但不能机械保护主动 final、needs_input、多轮独立审查及异常退出，因此不选它作为唯一防护。常驻 daemon 超出需求且引入额外生命周期，未采用。

### 追加嵌套实验限制（持久化与 ephemeral 均阻塞）

ephemeral root 的原生 supervisor 隔离上下文未获得 subagent-governance 所需 SessionStart 权威 session_id/CLI，按治理规则停在 nested prepare/spawn 前，worker 没有创建。root thread `01a0bf2c-8525-77a3-b5e1-9cc840ad032b`，supervisor native thread `01a0bf2d-8122-7d53-a43b-7d6bdd84002b`；只读证据位于同任务实验目录的 `host-probe-nested/`。唯一 supervisor 已完成并 closed，主 Agent 报告宿主退出 0。本次不证明嵌套查停支持。

主 Agent 随后在持久化隔离 carrier 重复验证，仍遇到相同缺口，排除了仅由 ephemeral 导致的解释。持久化 root `01a0bf2f-b942-7262-845f-01d3abc93fa7`，supervisor `01a0bf30-9df4-7e53-a6e4-d9510cac6177`，first ended `1789914055.782225`；原始证据为同任务实验目录下 `host-probe-nested-persistent/events.jsonl`。worker 从未创建，不能记为嵌套停止通过。

用户已将治理问题交由插件项目单独处理，并明确要求本仓继续自己的改动。新冻结需求允许本仓独立完成方案、实现及归档，外部真实嵌套集成标为“外部依赖待验证”。本仓不再调查或修改插件，不再运行该受阻嵌套实验，不借父 Session、不猜 Hook 身份、不跳过治理、不禁用或重组角色。测试替身仍必须驱动真实生产接口覆盖嵌套门禁；产品运行遇到同样能力缺口时仍 fail closed。跨宿主崩溃可恢复性也未获证明，只承诺安全对账/阻塞，绝不把外部未验证项计为 pass。

### 实施与归档路径边界

以下是 Stage 3 的 allowlist。路径只限定可修改位置，不授权无关修改。运行时规则、Skill 模板、协议说明、schema、README 和生成包都是产品行为，必须随实现候选一起接受测试和双轴审查；Stage 4 不补写这些行为。新模块与真实验收入口在此固定，普通内部实现细节仍由实现者决定。

- `README.md`
- `build/skill-packages.json`
- `src/stages/guided-implementation/scripts/workflow.py`
- `src/stages/guided-implementation/scripts/foreground_host.py`（新前台宿主适配模块）
- `src/stages/guided-implementation/scripts/workflow_stage_result.schema.json`
- `src/shared/scripts/workflow_progress.py`
- `src/shared/scripts/workflow_control.py`
- `src/shared/scripts/workflow_control_git.py`
- `src/shared/scripts/stage_dispatch.py`
- `src/shared/scripts/stage_handoff.py`
- `src/shared/references/workflow-progression.md`
- `src/shared/references/package-execution.md`
- `src/shared/references/guided-implementation/workflow-control-protocol.md`
- `src/shared/references/guided-implementation/worktree-execution.md`
- `src/shared/references/guided-implementation/originating-task-protocol.md`
- `src/shared/references/guided-implementation/execution-protocol.md`
- `src/stages/solution-design/SKILL.md.in`
- `src/stages/solution-design/references/subagent-protocol.md`
- `src/stages/solution-design/references/templates.md`
- `src/stages/guided-implementation/SKILL.md.in`
- `src/stages/change-closure/SKILL.md.in`
- `src/stages/change-closure/references/closure-protocol.md`
- `src/stages/change-closure/references/closure-actions.md`
- `tests/runtime/test_workflow.py`
- `tests/runtime/test_foreground_host.py`（新 transport 行为测试）
- `tests/runtime/test_workflow_progress.py`
- `tests/runtime/test_workflow_control.py`
- `tests/runtime/test_workflow_control_git.py`
- `tests/runtime/test_publication_progress.py`
- `tests/runtime/test_attached_flow_progress.py`
- `tests/packages/test_build.py`
- `tests/packages/test_cross_package.py`
- `tests/packages/test_preflight.py`
- `tests/host/verify_native_lifecycle.py`（新显式运行的真实产品适配验收入口，不加入默认单元测试）
- `tests/host/README.md`（隔离、权限、证据与外部待验证说明）

以下生成输出已按当前 build manifest 的资源 closure 与上述源文件逐项展开；加上新 foreground_host 的唯一 guided-implementation 输出，共 60 个精确路径。仅允许构建器由对应源生成，不允许目录级扩权或手改。source 与 generated changes 必须在同一实现提交。无需修改构建器、部署脚本、外部插件或安装位置。

- `skills/change-closure/SKILL.md`
- `skills/change-closure/package.json`
- `skills/change-closure/references/change-closure/closure-actions.md`
- `skills/change-closure/references/change-closure/closure-protocol.md`
- `skills/change-closure/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/change-closure/references/shared/guided-implementation/worktree-execution.md`
- `skills/change-closure/references/shared/package-execution.md`
- `skills/change-closure/references/shared/workflow-progression.md`
- `skills/change-closure/scripts/stage_dispatch.py`
- `skills/change-closure/scripts/stage_handoff.py`
- `skills/change-closure/scripts/workflow_control.py`
- `skills/change-closure/scripts/workflow_control_git.py`
- `skills/change-closure/scripts/workflow_progress.py`
- `skills/design-discussion/package.json`
- `skills/design-discussion/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/design-discussion/references/shared/package-execution.md`
- `skills/design-discussion/references/shared/workflow-progression.md`
- `skills/design-discussion/scripts/stage_dispatch.py`
- `skills/design-discussion/scripts/stage_handoff.py`
- `skills/design-discussion/scripts/workflow_control.py`
- `skills/design-discussion/scripts/workflow_control_git.py`
- `skills/design-discussion/scripts/workflow_progress.py`
- `skills/guided-implementation/SKILL.md`
- `skills/guided-implementation/package.json`
- `skills/guided-implementation/references/shared/guided-implementation/execution-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/originating-task-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/guided-implementation/references/shared/guided-implementation/worktree-execution.md`
- `skills/guided-implementation/references/shared/package-execution.md`
- `skills/guided-implementation/references/shared/workflow-progression.md`
- `skills/guided-implementation/scripts/foreground_host.py`
- `skills/guided-implementation/scripts/stage_dispatch.py`
- `skills/guided-implementation/scripts/stage_handoff.py`
- `skills/guided-implementation/scripts/workflow.py`
- `skills/guided-implementation/scripts/workflow_control.py`
- `skills/guided-implementation/scripts/workflow_control_git.py`
- `skills/guided-implementation/scripts/workflow_progress.py`
- `skills/guided-implementation/scripts/workflow_stage_result.schema.json`
- `skills/problem-framing/package.json`
- `skills/problem-framing/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/problem-framing/references/shared/package-execution.md`
- `skills/problem-framing/references/shared/workflow-progression.md`
- `skills/problem-framing/scripts/stage_dispatch.py`
- `skills/problem-framing/scripts/stage_handoff.py`
- `skills/problem-framing/scripts/workflow_control.py`
- `skills/problem-framing/scripts/workflow_control_git.py`
- `skills/problem-framing/scripts/workflow_progress.py`
- `skills/solution-design/SKILL.md`
- `skills/solution-design/package.json`
- `skills/solution-design/references/shared/guided-implementation/workflow-control-protocol.md`
- `skills/solution-design/references/shared/guided-implementation/worktree-execution.md`
- `skills/solution-design/references/shared/package-execution.md`
- `skills/solution-design/references/shared/workflow-progression.md`
- `skills/solution-design/references/solution-design/subagent-protocol.md`
- `skills/solution-design/references/solution-design/templates.md`
- `skills/solution-design/scripts/stage_dispatch.py`
- `skills/solution-design/scripts/stage_handoff.py`
- `skills/solution-design/scripts/workflow_control.py`
- `skills/solution-design/scripts/workflow_control_git.py`
- `skills/solution-design/scripts/workflow_progress.py`


Stage 4 文档更新仅限以下六份规划文档的实际结果、Lifecycle/完成状态、候选与检查证据和外部依赖待验证说明，不改变方案语义或验收标准：

- `.scratch/native-agent-lifecycle/PRD.md`
- `.scratch/native-agent-lifecycle/issues/01-retain-foreground-host.md`
- `.scratch/native-agent-lifecycle/issues/02-live-decisions-and-stop-barrier.md`
- `.scratch/native-agent-lifecycle/issues/03-reconcile-lost-host.md`
- `.scratch/native-agent-lifecycle/issues/04-end-to-end-host-acceptance.md`
- `docs/adr/0009-retain-foreground-host-across-native-agent-turns.md`

Stage 3 与 Stage 4 都保护冻结需求 `docs/requirements/2026-09-20-native-agent-lifecycle.md`。CONTEXT 和已有 ADR 不需要修改。真实实验原始日志保存在现有 run 的隔离证据目录，不把日志当实现源或新增权限账本。

### Stage 2 文档验证记录

Repository validator、暂存差异检查、构建一致性检查与五个 Skill 校验已通过。规划阶段启动的 `./scripts/validate.sh` 运行时全量测试按主 Agent 明确决定主动中止，未完成；中止前无失败输出、观察到一项 skip，不据此声明全量通过。仅本轮测试 PID 64781、64800 及其当时明确子进程 66859 已结束，无残留；未删除其他产物。Stage 3 必须对最终实施候选完整运行 `./scripts/validate.sh`，本次中止不减免该要求。

### Stage 4 implementation acceptance and closure

T1-T4 are complete for this repository. Accepted candidate: `35d0c24d3729d6487751d80baabea1310a69a43a`; both independent Standards and Spec reviews accepted this exact candidate. This record changes no D1-D7/A1-A6 requirement.

- [x] One foreground host survives carrier turns; native followup retains the original identity. Events are strictly attributed, effective configuration is checked, version-4 / workflow-progress-v6 boundaries and generated packages are synchronized.
- [x] One owner consumes exact queued decisions. Stop takes priority over unsent responses; current and historical candidate reviewers participate in the stop barrier. Late activity invalidates old stop receipts.
- [x] Lost host, interrupted receipts and uncertain sends preserve original actions and Git facts; recovery reconciles publication/cleanup without duplicate dispatch or publication.
- [x] Review fixes validate malformed native event containers/aliases, prioritize queued stop, include historical reviewer identities, and bound nonblocking pipe writes. The first positive byte write commits the frame; zero-byte EAGAIN waits outside checkpoint locks and rechecks stop. Partial timeout remains uncertain and poisons the connection against replay.

Evidence index: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/candidate-delivery.json`; review handoff: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/stage4-handoff.json`.

- [x] full: 671 tests, passed; log `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-full-validation-passed.log`.
- [x] focused: 28 tests, passed; log `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-frozen-focused.log`.
- [x] pipe_and_priority: 6 tests, passed; log `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-final-focused.log`.

The full `./scripts/validate.sh` run took 1828.058 seconds and ended OK with one existing NonGit fixture skip. Deterministic build/check, five Skill validations and repository validation passed (5 Skills, 81 references, 33 test modules). Earlier 666-test results belong to the previous candidate and are not substituted for this 671-test result.

- [x] Real single-layer lifecycle: completed, local_exit_code 0; report `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-lifecycle/report.json`, raw events `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-lifecycle/events.jsonl`.
- [x] Real single-layer stop: completed, local_exit_code 0; report `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop-retry/report.json`, raw events `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop-retry/events.jsonl`.

These current reports use codex-cli 0.155.0-alpha.9.2, product module SHA-256 `623944f519ba0c2ea6e007bed8c5f9f96764f1725725d50dcc70eace7ed4c84a` and candidate guided-implementation package digest `d2b92636767a77aa551107c6dfacfdcd71bce99df954f19f9c71c339ccd8feff`.

The first stop experiment failed during local cleanup: PermissionError EPERM on SIGKILL of process group 9013. Cause is not established; this attempt is not a pass. Failure receipt: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop/failed-execution.json`; stderr: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop/failed-execution.log`; raw events: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop/events.jsonl`. Read-only inspection found the original PID/group absent. One Controller-authorized isolated retry with unchanged module/package passed at `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/backpressure-host-stop-retry/report.json`. This does not establish that the original cause was fixed.

Complete stage 2-to-3-to-4 runner/C/B/Git and unique publication/cleanup are verified with substituted external host transport. Real reports certify single-layer product-adapter/native lifecycle only, not a fully model-driven nested business run. Real nested integration remains pending external dependency, owned by the external governance project; the user explicitly made it nonblocking for local repository delivery. It is not counted as passed. Named permission profiles without complete effective-rule readback fail closed. Loss of the original foreground owner cannot revive old native identities: missing native lookup/action reconciliation retains evidence and blocks continuation. No installation, deployment, remote publication, external plugin change, old-run migration or old download rerun is claimed.

### Stage 3 regression path supplements

The Controller supplemented the original 95-path implementation scope to 99 paths solely for existing regression modules: `tests/runtime/test_accepted_query_retry.py` and `tests/runtime/test_pending_host_actions.py` (95 to 97), `tests/runtime/test_unified_recovery.py` (97 to 98), and `tests/runtime/test_business_recovery.py` (98 to 99). Original settlement-before-stop, no unknown-call replay, fresh proof and anti-forgery requirements remain unchanged.

Authority: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/stage2-checkpoint.json` field `authority_addendum`; exact chained records: `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/stage3-test-path-addendum.json`, `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/stage3-test-path-addendum-2.json`, `/Users/zhaolaiyuan/.codex/workflow-runs/01a0bdd5-8915-70e0-8e0c-027e96ccd1dd/native-agent-lifecycle/stage3-test-path-addendum-3.json`. Frozen requirements remain unchanged.
