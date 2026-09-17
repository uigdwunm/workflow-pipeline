# 五个工作流 Skill 独立安装实施方案

Status: implemented — archived; T13/T14 field acceptance not run

需求依据：冻结的《工作流 Skill 独立安装需求》，提交 `78913735a91dcd4c3b343c0a097e2afa05981b36`，SHA-256 `9135e852db58431910ead3fe0ad096fe5b5dca5ff664a6d385d25b39bca673df`。
本文保留已接受实施方案及历史设计依据；实现已通过 Stage 3 双轴审查，并进入授权归档。下方测试定义保留为验收合同，实际证据及未验证边界见归档记录。

## Problem Statement

用户只安装一个工作流 Skill 时，当前入口仍读取其他工作流安装目录中的规则和脚本。阶段自身不可独立运行，而且“磁盘上存在”“当前 Agent 可调用”“版本兼容”被混为一谈。项目链接漏检会把已安装的 Matt 能力误报为缺失。要求用户安装五个同版入口能够掩盖这些问题，却不能满足按需安装。

源码复用与发布自包含并不矛盾：需要改变资源组织和解析边界，不改变 Workflow Stage 的业务职责。尤其 Stage 3 仍只实现、测试、审查、形成候选并保留 Flow Worktree；Stage 4 仍负责最终发布、归档和清理。

## Solution

将现有五个入口改为从单一源码生成的五个自包含包。每包只有自己的用户入口，携带其阶段执行需要的内部规则、脚本和传递依赖；不携带其他阶段入口或 Matt 内容。用户可以只安装任意一个包，在当前阶段必要的 Matt 能力与既有基础环境齐备时完成该阶段。

本阶段内部调用始终定位本包资源。只有真正进入另一个阶段时才按当前 Agent 的有效 Skill 注册结果发现目标包并核验交接协议。单阶段完成后，缺少下一阶段只影响下一阶段启动；连续模式在最早可确定路径时检查后续安装，在每次交接时复核，遇阻保留已完成成果和既有恢复点。

## User Stories

1. 作为只需要讨论的用户，我希望安装 design-discussion 即可维护一个 Discussion Topic，不因缺少实现阶段而失败。
2. 作为只需要拷问的用户，我希望 problem-framing 使用已安装的 Matt 能力完成唯一需求文档，并独立提交冻结结果。
3. 作为已有需求的用户，我希望 solution-design 独立完成 Spec、必要 ADR、Tickets 和规划发布。
4. 作为已有明确实现请求的用户，我希望 guided-implementation 保留现有 standalone 入口、实现和双轴审查能力。
5. 作为只需要归档的用户，我希望 change-closure 支持既有 standalone 和继承候选两条路线。
6. 作为用户，我希望 Stage 3 缺少 Stage 4 时仍可交付被验证的候选和保留工作区，不替我合并。
7. 作为组合安装用户，我希望跨阶段仍使用同一需求身份、Workflow Controller 和 Flow Worktree。
8. 作为讨论附着流程用户，我希望不同包操作同一个 ledger，而不是各自产生讨论状态。
9. 作为使用符号链接安装的用户，我希望合法项目链接和全局链接都能正确识别。
10. 作为用户，我希望“已安装但当前任务未注册”与“未安装”“不可读”“不兼容”有不同诊断。
11. 作为用户，我希望未调用的 Matt 分支不成为当前阶段启动门槛。
12. 作为用户，我希望缺少必要 Matt Skill 时得到具体名称、用途和恢复位置，不被静默替代或自动安装。
13. 作为连续执行用户，我希望开始时就知道后续阶段不齐，且安装修复后不用重复已完成工作。
14. 作为用户，我希望运行期间切换安装链接不会悄悄换掉正在执行的规则和脚本。
15. 作为维护者，我希望改一次共享源码即可确定性生成全部受影响包。
16. 作为维护者，我希望构建拦住遗漏资源、越界链接、错误导入和手工修改生成副本。
17. 作为发布者，我希望 Git 安装发现只看见五个生成入口，源模板不成为重复 Skill。
18. 作为维护者，我希望隔离测试真正运行脚本和 Git/ledger 行为，同时明确与现场 Agent 验收的区别。
19. 作为升级用户，我希望能保留旧运行所需包；旧 runner 记录不会被新程序猜测迁移或重新启动。
20. 作为并行用户，我希望不同包副本共享既有锁，继续拒绝过期 revision 和发布竞争。

## Implementation Decisions

### D1. 唯一源码与唯一可发现发布面

采用“源模板 + 共享组件 + 已生成安装包”三层，生成包纳入 Git，便于现有从仓库安装的工具直接消费。源码入口文件使用模板后缀，不命名为可发现的 SKILL.md；整个仓库可安装入口必须恰好是发布面下五个入口。源模板不是第六套可安装包。测试夹具如需要同名入口，只在临时目录动态创建，不提交第二套入口。

构建器负责五个固定包，不发展成通用包管理器。人工只编辑源模板、共享源码和显式构建清单；产物由构建生成。构建不读取本机安装目录、活动 registry、用户配置或网络。输入是被跟踪的源文件与清单；输出是包目录和内容清单。排序、UTF-8/LF、权限规范化；内容摘要排除时间戳、绝对路径、Git 未跟踪文件和自引用摘要。两次构建必须逐文件同字节、同模式。`--check` 在临时目录重建并比较，不改工作区。

每包 manifest 标记包名、发布版本、包格式版本、源修订说明、资源闭包、每文件摘要、内部协议兼容键和本阶段外部能力要求。包内容摘要取规范化文件表的摘要，manifest 本身不参与自引用；这是完整性和固定执行身份，不是签名或供应链认证。禁止产物中的符号链接、硬编码开发机路径、其他阶段入口、Matt 源码与测试文件。安装包根本身可以是合法符号链接。

### D2. 内部资源闭包与代码归属

共享能力按现有职责归属：需求/确认/拆题/生命周期规则，discussion 协议，Workflow Control 纯规则与 Git 证据适配器，thread settings，supervision/worktree 协议。共享源独立于任一用户入口；分发复制不改变文档权威。

每包显式列出自己的入口、阶段引用、需要的共享引用与脚本根；构建沿内部资源引用和 Python import 取得传递闭包，未声明动态依赖即失败。闭包计算使用显式清单作边界和审计依据，不用“复制整个其他 Skill 目录”兜底。文档中的流程去向是按名称调用外部阶段，不追踪成另一个 SKILL.md；本阶段必须读的合同则复制为内部引用。混合文档可保留现有共享协议整体，不为缩小几个字节拆改业务规则。

当前 discussion CLI 在导入时加载完整命令表、Workflow Control 及 Git 适配器；后者可调用 supervision 验证。因此只要本阶段支持 discussion-attached 路线，就必须包含整个 discussion Python 模块闭包以及其控制/Git/supervision 传递依赖。五个阶段都保留该路线，所以五包都带这组运行资源。它们只是内部协议实现，不赋予入口所有命令的使用权限；既有角色、Phase Run、路径权限照旧约束。此轮不为减包体重写 dispatcher 或增加按操作裁剪机制。

| 包 | 本阶段特有内容 | 必需共享运行闭包 | 不打包的内容 |
| --- | --- | --- | --- |
| design-discussion | 讨论循环、子话题、topic 模板与专有规则 | discussion + control/Git + supervision；角色配置涉及 thread settings 时包含该模块 | 其余四个入口、runner、Matt |
| problem-framing | dedicated grilling、需求冻结与恢复规则 | discussion + control/Git + supervision + thread settings | 其余四个入口、runner、Matt |
| solution-design | conversation source、designer 协议、readiness 与模板 | discussion + control/Git + supervision + thread settings | 其余四个入口、runner、Matt |
| guided-implementation | dispatcher、execution、review 与恢复规则；现有前台 runner 及结果 schema | discussion + control/Git + supervision + thread settings | 其余四个入口、Matt |
| change-closure | closure 协议与动作 | discussion + control/Git + supervision + thread settings | 其余四个入口、runner、Matt |

所有包还含同源的包身份/依赖预检辅助器。Stage 0 的 thread settings 依其 Workflow Control 配置路径纳入，不靠当前讨论是否触发该分支省略。共享文档闭包包括五包所需的阶段附着合同，不能只复制从入口可直接看到的一层链接。许可证和第三方声明按实际打包内容保留；Matt 仅为外部依赖声明。

### D3. 脚本与文档本地化接口

发布包内采用固定本地脚本布局，保留现有 CLI 名称和 stdin/stdout JSON 合同。将共享控制、Git 适配器、supervision 和 discussion CLI 放在同一个本包 scripts 目录；discussion_core 的控制适配器改为正常导入本地 control/Git 模块，删除按兄弟 Skill 路径加载及全局模块注入。supervision_core、discussion_core 保留包内相对导入。Git 适配器调用 supervision 的同目录定位继续有效。入口脚本从自身真实路径建立唯一的本包脚本搜索根；不依赖 CWD、仓库根或 PYTHONPATH，不允许从其他安装包回退导入。

测试使用独立子进程执行每包，避免同进程 sys.modules 缓存串包。运行接口输入/输出、操作 allowlist 和错误语义保持原状；只改变资源定位。现有带来源目录的文档脚本命令、inline code 路径、Markdown 链接和模板中的路径均改为本包位置。不能只替换 Markdown 链接却留下文本里的旧命令。源模板使用显式资源标记，由构建清单解析为输出文件的相对路径；无法解析的标记、链接锚点或静态资源引用使构建失败。普通外部 URL 和 `$下一阶段` 不转为内部副本。

运行前固定包根的 realpath；合法包根链接可跨目录，包内引用经解析后必须仍在该包真实根内。不存在、循环、悬空链接、不可读文件、错误入口名称和包摘要不符分别报错。验证安装路径并不放宽需求文件、实施文件和 Git 证据本来禁止符号链接的规则。

### D4. 当前能力发现与 Matt 依赖

新增一个小型只读预检边界，输入为当前阶段、即将执行的动作/分支、目标阶段集合（如有），以及由当前可信宿主提供的有效 Skill 注册条目（名称、入口路径、来源）。输出已解析身份或结构化错误，不写安装目录、ledger 或 Git。预检不将用户文本中的注册声明视为可信运行证据。

优先级固定：当前 Agent 有效 registry 选中的精确名称/路径 > 对该路径的文件校验；文件系统扫描只能诊断候选，不能覆盖 registry 的选择，也不能把磁盘候选宣称成可调用能力。如果宿主只提供当前任务的 Skills 列表，用该列表及其精确链接；原生子 Agent 在自己的动作前再按自己的有效列表核验。CLI 没有 registry 时可接收控制器提供的当次解析清单，输出必须区分“文件可读”和“Agent 可调用”；缺少可信当前 registry 时不伪造成功，返回 registry_unavailable/skill_not_active 并指出刷新当前任务后重试。

诊断扫描只查明确的项目 Skill 根及现有支持的用户根，跟随候选目录/入口的合法 symlink，以最终真实文件身份去重，不全盘搜索、不仅检查普通目录、也不引入新的优先级。两个链接指向同一文件不是重复；多个不同候选存在时，如果 registry 已唯一选定则报告其他候选但不拦住执行；registry 本身歧义或没有选择时不猜选。实际读取权限通过打开文件确认，不能用 exists 代表可读。

Matt 的检查是“当前动作必要能力”，不把全 Matt 仓库变成统一安装门槛，也不解析改写第三方 Skill 以制造自建替代：

| 当前阶段/动作 | 检查时点与必要 Matt 能力 |
| --- | --- |
| Stage 0 原生讨论流程 | 无无条件 Matt 依赖；只有显式选择某 Matt 能力时才检查该能力 |
| Stage 1 路由 | substantive 工作前检查 ask-matt；路由决定 grill-with-docs 后，调用前检查 grill-with-docs 及其实际需要的 grilling/domain-modeling；选择其他合法 Matt 路由时检查该路由要求，禁止借路由进入 Spec/实现 |
| Stage 2 Spec | 写 Spec 前检查 to-spec；决定 Tickets 时检查 ask-matt；决定需要 Tickets 后、生成前检查 to-tickets；必要 ADR 使用 domain-modeling 时才检查它 |
| Stage 3 | dispatcher 启动前检查 implement、tdd 和必需的 code-review；code-review 各轴实际使用的能力由该已安装 Matt Skill 定义，子 Agent 读取后逐一核验；不修改第三方，也不要求无关 grilling/Spec |
| Stage 4 | closure 路由调用前检查 ask-matt；选中的文档操作依其实际使用能力检查，不要求实现/Spec/grilling 全套 |
| 项目 Matt 配置 | 先读已有 tracker/triage/domain 约定；只有确需首次 setup 时提示 setup-matt-pocock-skills，不因正常阶段不需要重配置而检查或自动执行它 |

错误提供 stage、action、required_skill、登记入口/扫描候选、原因和恢复点。修复方式是用户安装/启用必要能力、刷新 registry，然后重试该动作；不自动安装、不补做 Matt 逻辑、不把缺失外部能力算作包内部缺陷。必要能力选择后先检查再产生该动作的副作用；已经完成的前序动作不回滚。

### D5. 阶段发现、runner 与固定执行身份

真正跨阶段时，通过同一预检边界从当前 registry 解析目标阶段包。固定五个名称/阶段映射，禁止按当前文件的父目录拼出下一阶段路径。目标入口名称、包 manifest、实际可读资源、内容摘要、协议兼容性全部通过后，才能准备目标 dispatch。普通提及/建议安装下一阶段不要求其可用。

本阶段入口在开始时固定本包身份；连续模式还固定所需目标包身份。身份包括 stage/name、规范绝对入口和根、package format、release、bundle digest、compatibility key。控制器把它附在既有 conversation checkpoint 或 runner confirmed 信息中，原生子 Agent 继承并重新校验；不创建安装状态中心、第二套 execution registry 或额外 ledger。运行中每次启动脚本、恢复会话、转交目标前复核固定身份，不再次跟随已切换的安装链接选择新版本。变动即 package_changed/package_unavailable，保留当前检查点，不继续加载替换代码。

升级按“安装到新的不可变版本目录 → 切换新任务使用的注册链接 → 保留旧版本直至运行结束”进行；不用本项目实现安装器。人工原地覆盖不受支持，检测到摘要变化停在原恢复点；恢复原身份的文件或结束当前尝试后按既有恢复协议重新确认绑定，不能将同一运行悄悄升级。当前已加载进程不能防御任意用户并发改文件，因此约定不可变安装目录，并用启动/恢复边界检查防止静默混用；不承诺对恶意实时修改提供 OS 级快照隔离。

前台 runner 仍只属于 Stage 3 包，仍负责既有 Stage 2→3→4 连续路径。开始需要显式当前 registry 解析结果；runner 不自行查全局目录。新增 run record version 2，在既有 confirmed 中保存 runner 自身身份及三阶段包身份，`_stage_prompt` 只使用这些已验证入口。既有 schema 的业务 handoff、阶段模型配置和 authority 字段不变。start 在写新记录和启动 Stage 2 前预检全部三阶段；resume 先拿既有 run lock，再验证记录及固定身份，保留 sessions、stage_results、history 和所有已完成 handoff。

旧 version 1 记录没有可信固定包身份，新 runner 只读识别后报 legacy_run_requires_original_runtime，不原地补猜身份、不改写、不重启阶段、不清理 worktree。恢复使用原旧版本 runner 和旧安装树完成该运行；文档提醒升级前保留旧发布。如旧运行文件已丢失，停在原记录和 Git 证据上由控制器按现有恢复协议处置，不自动迁移。这是明确的兼容边界，而不是自动升级功能。

### D6. 缺失阶段与恢复顺序

| 情景 | 决策与可观察结果 |
| --- | --- |
| 显式单阶段入口 | 只预检本阶段必需能力；其余四个未装不影响进入、成功或本阶段发布 |
| 单阶段成功，后续未装 | 完成本阶段原有提交/验收，提供真实 handoff、保留状态和目标名；下一阶段标记不可开始；不把“本阶段完成”写成“全部完成” |
| 请求连续执行，尚未开始本次阶段 | 冻结实际路线后、任何新的 carrier 创建/本次 worktree 创建/流程激活之前，预检整个剩余路线；缺失则不开始连续运行，列缺项和可恢复入口。用户可明确改为单阶段；不静默降级 |
| 当前阶段已经开始，用户改为连续 | 检查剩余路线；缺失不取消正在执行且已授权的当前阶段，完成当前阶段并保留；连续请求记录为被依赖阻塞，不声称已获许可换范围 |
| 起始预检通过，目标在交接前消失/不可读/不兼容 | 先保存当前阶段已验证成果，再阻止下一阶段 prepare/dispatch；保留同一 Flow Worktree、accepted result、待归档 carrier。不得因为目标缺失而归档旧 carrier 或推进 Phase |
| 目标已启动后自身资源消失或变更 | 当前动作失败，使用既有 technical-error / outcome-unknown 语义；先核验运行实体是否停止和副作用，禁止重派。恢复仍是同一 session/attempt/binding |
| 修复依赖后重试 | 重验 source/hash、current registry、目标包及原绑定；从第一项未完成动作恢复，不重做已完成 Spec/Tickets、候选、merge 或需求冻结 |
| 最终合并已成功但归档/清理未完成 | 继续既有 cleanup-only/accepted-result recovery；不得因包安装问题重新发布或创建新 Flow Worktree |

没有后续可用阶段时，不改 Stage 3 的“保留候选”职责为发布或 cleanup。附着讨论仍遵守 prepare→claim/ready→source activate→accept/finalize；安装成功只是执行能力，不是进入权限，也不提前改变 current_phase。

### D7. 协议兼容、共享状态与锁

独立包版本允许不同，但只采用最小兼容策略：相关包的协议兼容键必须完全相等，不引入版本范围求解、迁移框架或降级执行。键包含 handoff/role contract、discussion 请求及 ledger 读写 schema 支持、control/context、supervision、thread settings 的确切协议代际。构建从共享权威定义生成，文档-only 发布可以保持键不变；改变这些可交换语义的发布必须提升键。包发布版本只是诊断，不能替代兼容证明。

跨阶段键不等或缺 manifest 的旧式目标包一律在交接前拒绝，提示安装兼容目标；单独执行完整旧包与本次新包之间不宣称互通。第一版不自动迁移旧控制 handoff；已有记录/ledger 的现有读取和写升级兼容仍保持。Stage 3 独立入口不读未来阶段 manifest。

所有分发副本使用原有 canonical project/Git common directory 计算 ledger、registry、checkpoint 和锁位置，不含包名、版本、安装根或发布目录。discussion 仍唯一写 ledger；supervision 仍只用 Git 和短 publication lock；runner 仍只持有同一 record 旁的 run lock。跨包兼容性检查不代替修订号、authority 和锁内新鲜度检查。schema 不支持必须在写之前拒绝，不能把不同包副本当成不同 topic store。

不新增跨协议大锁，不同时长期持有 discussion 与 publication lock。保留已有短锁顺序和分别重验语义，避免为了“同版”制造另一个状态所有者。相容不同发布包并行写同一 ledger：一个提交、另一个按既有 stale revision/idempotency 分支处理；并行 publish：一个成功、另一个 target_changed/publication_busy 且保留工作区。未来有不兼容 schema 发布时必须另做兼容/迁移设计；本轮不写未知字段或提升 ledger schema。

### D8. 变更契约预检与矛盾处理

| 生产入口与当前调用链 | 新 owner / 接口 | 状态与顺序 | 依据与处理 |
| --- | --- | --- | --- |
| 五个 SKILL → references → shared contracts | 构建器 + 资源清单；源标记→包内路径，缺漏 fail | 无业务状态，构建时先闭包后产物校验 | AC1/2；源码跨目录允许，发布执行跨包内部读取禁止 |
| discussion CLI → discussion_core control → control/Git → supervision verify | 本包 runtime；原 JSON 命令保持 | 仍读写原 ledger；身份校验、锁、revision 均保留 | AC3/6；完整导入闭包不能只复制 CLI |
| Stage 2/3/4 → settings / supervision / Git adapter | 本包 runtime；固定根，不用 CWD | 同一 Flow Worktree，只有 Stage 4 complete | AC3/6；Stage 2 publish-planning 是原有例外，不扩充 Stage 3 |
| 阶段 Matt 调用 → 当前有效 registered Skill | 只读 preflight；动作+registry→身份或诊断 | 调用前检查，失败不执行该动作；先前完成保留 | AC4；不能用全局文件扫描取代 Agent registry |
| footer/continuous controller → 目标 stage | stage preflight；目标名+兼容键→固定 identity | 后续 readiness/activation 仍由原协议负责 | AC5/6；缺下一阶段不否定本阶段完成 |
| runner start/resume → stage prompt → codex session | runner + 固定 identities；record v2 | run lock→记录校验→包校验→同 session；结果先持久化再推进 | AC5/7；v1 不猜测升级 |
| 两包 discussion / supervision 操作 | 既有 state / Git owners | 同一 canonical root/lock；不新增安装锁代替业务锁 | AC6；复制代码不复制状态 |
| repository validator / release checks | 源验证与发布验证分别拥有 | 源测试发现不依赖生成副本；产物漂移阻断 | AC2/7；不能以现有源码布局检查代替安装可用性 |

已解决的重要矛盾：同版安装要求替换为相关协议键兼容；五 Skill 全量依赖检查替换为当前动作必需能力；兄弟脚本定位替换为包内定位或真正外部阶段解析；源码与产物同时暴露入口替换为模板与唯一可发现发布面。项目 ADR-0001/0005/0006/0007 的状态、角色、gate 和 worktree 规则不变。

## Testing Decisions

测试以真实发布入口为最高可行 seam：生成包→临时单包安装→实际 CLI 子进程→临时 Git/ledger 的可观察结果。resolver 只用可控有效 registry 输入替代宿主发现；runner 用现有受控 executor 验证调度、故障和恢复，不能声称它执行了真实 Agent 推理。已有 unittest、临时 Git 仓库、JSON 子进程与并发 publication 测试可复用。以下每项都要在实现后取得证据。

| ID / 条件 | 层级与场景 | 必须观察的结果 |
| --- | --- | --- |
| T1 / AC1,2,7 | 静态构建：干净构建两次；改单个共享源；对产物手改/删资源 | 字节确定、受影响包摘要变化、未受影响包不变；check 拒绝漂移/缺漏 |
| T2 / AC1,2 | 静态：递归入口发现、资源闭包、链接/锚点、inline 命令、import、manifest | 仓库恰好五个 SKILL.md；每包一个入口；不含 Matt/其他入口/越界 symlink/旧兄弟路径；故意坏链接/动态未声明依赖失败 |
| T3 / AC3 | 真实脚本：五次独立临时环境；只有一个发布包，源码不可达，清除 PYTHONPATH，任意 CWD 含空格/非 ASCII | 每个实际 CLI 能导入和执行其有效只读/最小写操作；不是仅 --help 或文件存在检查 |
| T4 / AC3,6 | 真实 discussion CLI：不同包执行 bootstrap/read、需求 DW、Phase Run、workflow-control 及 Git delivery 验证 | 同一个 ledger/topic/需求，原权限与哈希检查有效；非讨论阶段 standalone 不凭空建 ledger |
| T5 / AC3,6 | 真实 supervision：临时仓库 start/verify/publish-planning/complete，测试角色路由 | Stage 2 保留 worktree，Stage 3 候选不合并，Stage 4 发布并清理；旧 source 与 protected paths 保持 |
| T6 / AC4 | resolver/真实文件：项目/全局合法目录链接、入口链接、两链接同源、多个真候选、registry 胜出、disabled/not-active、循环/悬空/不可读 | 合法链接不误报；唯一当前注册选择可靠；错误分类稳定；仅磁盘候选不能装成 active |
| T7 / AC4 | 按阶段/分支 Matt 预检：缺当前必需、缺条件能力、缺未用能力 | 只在相应动作之前失败；返回准确名称和恢复点；无自动安装/替代/第三方改写 |
| T8 / AC5 | 单包运行及缺后继：每个阶段独立；成功 footer；连续初始缺项 | 单阶段可成功；连续开始前无新增 carrier/worktree/activation；不静默改为单阶段 |
| T9 / AC5 | runner/控制器故障注入：预检后删除目标、改包字节、切换链接、权限丢失；执行结果已保存后目标失败 | package 错误停在第一未完成动作；stage_results 不回退；恢复不重复 dispatch/commit/merge |
| T10 / AC5,7 | v2 start/resume；v1 旧记录；不同 release 同 key、不同 key、无 manifest | v2 固定身份恢复；v1 只读明确拒绝；同键跨包可用、异键禁止目标启动 |
| T11 / AC6 | 两个不同安装包的独立进程同时写同 ledger/同 Git common dir，分别含同键不同 release；两个 runner 同 record | 锁路径实际相同；无丢更新、无双发布；stale/busy/target_changed 与 idempotent ACK 正确；run_busy |
| T12 / AC7 | 发布工艺：源测试发现、quick validator、重建漂移、临时安装发现、干净 Git 检查 | 五包产物与 README 的安装目标一致，源模板不可安装，无本机私有状态参与 |
| T13 / AC3–7 | 现场 Agent 验收，真实 host/当前 registry/Matt，五个分别只装单包的新任务 | 能走各阶段关键路径，正确读取本包引用；保留 transcript、包摘要、依赖清单和实际结果 |
| T14 / AC5,6 | 现场组合验收：2→3→4 及一个 discussion-attached 路线；运行中阻断后继再恢复 | 验证原生角色/真实 handoff、同一需求与 Flow Worktree、旧 carrier 归档时点；未代做缺失阶段 |

T3–T5 的包内 CLI 真实运行只证明机制，不证明整个 Agent 已完成业务；T9 的 fake executor 不算 T13/14。现场验收必须用户授权真实测试项目和任务副作用，使用可丢弃项目；自动化实现不得默认现场条件已满足。缺少现场环境时标记“未验证”，保留可执行检查步骤和自动化证据，不宣称 AC3 的完整 Agent 行为通过。运行基础前提沿用 Git、Python、原生子 Agent 和既有 CLI/宿主，不新增 Windows 适配承诺。

发布门槛为 T1–T12 自动检查通过；宣称“现场独立可用”还须 T13/14 对应证据。失败聚焦受影响检查重跑，不机械反复全跑。测试不扫描作者私有安装来证明普遍可用。

## Out of Scope

不内置、fork、重写、自动安装 Matt；不修改 codex-manager 或第三方 installer；不新增通用安装器、依赖求解器、自动更新服务、后台 monitor；不改变五阶段业务职责、模型路由、文档权限或 Git/ledger 权威；不新增平台适配；不实现 version 1 runner 的自动迁移；不更改用户全局安装。本轮已完成获授权的实现与归档；部署、远程发布和用户安装变更仍不在范围内。

## Further Notes

### 具体布局与构建/发布约定

这里记录实施定位证据与具体布局；正文 Implementation Decisions 的接口决定不绑定现有路径。

- 单一源码：`src/stages/<name>/SKILL.md.in`、同目录阶段 references/agents；`src/shared/references/`、`src/shared/scripts/`；测试迁入 `tests/` 并按职责分组。
- 构建输入：`build/skill-packages.json` 明确五个包的根、资源边、脚本根、条件 Matt 能力和协议键；构建实现 `scripts/build_skills.py`。
- 唯一安装面：`skills/<name>/SKILL.md`、`references/`、`scripts/`、`agents/openai.yaml`、`package.json`。包内共享文档位于 `references/shared/`，专属文档保留单独相对位置。五包名称保持不变。
- 验证入口：保留 `scripts/validate.sh` 作为维护者总入口；更新 `scripts/validate_repository.py` 分别认识源模板、源测试及产物，只从源测试目录收集测试；新增构建 `--check` 与隔离安装测试。`scripts/check-dependencies.sh` 改为只读诊断前端，支持 stage/action 与明确项目根；输出明确 filesystem-only，不能给 active registry 成功背书。正式 Agent preflight 直接用包内 `scripts/skill_preflight.py` 的结构化接口。
- 开发：修改 src → build → check → 源测试和安装隔离测试 → 提交源与产物一起。不得人工分别编辑五个副本。release 使用仓库同一提交下已验证的 `skills/<name>`；可打成五个归档，归档内容必须与该目录逐文件一致。无需新增远程发布平台。
- 安装器可以扫描仓库也可以消费单个目录；两者都只能发现生成入口。不在 src 下留名为 SKILL.md 的兼容入口。旧源码路径迁移对维护者是明确破坏性目录调整，但用户可安装路径 `skills/<name>` 保持。
- README 更新逐包安装路径、基础环境、Matt 按能力要求、单阶段/连续模式区别、诊断输出、不可变升级和旧 runner 恢复。现有从 Git 安装命令的有效性在实施时用实际 installer 的固定版本做发现测试；不猜测新 CLI 参数。
- 本机同步验收在实现完成后另获用户针对安装更新的授权：先记录当前有效 registry/真实链接与旧包摘要，备份可恢复身份，在独立临时安装根验证，再按用户实际管理工具同步生成包并刷新任务列表；读回当前 registry 与摘要。不得擅自编辑 manager 私有数据库或认为磁盘复制成功等于当前会话已生效。

### 规划时生产调用链证据（历史基线 78913735a91dcd4c3b343c0a097e2afa05981b36）

| 证据 | 硬耦合/权威及处置归属 |
| --- | --- |
| `skills/design-discussion/SKILL.md:17,38` | 讨论入口读兄弟 control 合同，再调用自身 discussion CLI；D2/D3 本地化 |
| `skills/problem-framing/SKILL.md:13,32,37,43,114` | control、需求、拆题、生命周期、确认跨目录；D2/D3 的共享文档闭包 |
| `skills/problem-framing/SKILL.md:131–143` | ask-matt 选择实际 Matt 路由；D4 条件预检，禁止越阶段 |
| `skills/solution-design/SKILL.md:18–27` 与 `references/subagent-protocol.md` | worktree、确认、需求、control 和 thread-settings 合同依赖；后者明确 to-spec/ask-matt/to-tickets；D2–D4 |
| `skills/guided-implementation/SKILL.md:16,143,193,223` | 确认/生命周期共享；implement/tdd 与 code-review 必需；D2/D4 |
| `skills/change-closure/SKILL.md:12–16,37,65` 与 `references/closure-actions.md` | 共用 worktree/确认/control/lifecycle；ask-matt 条件路由与 Stage-4 complete-worktree；D2/D4/D6 |
| `skills/design-discussion/references/lifecycle-integration.md` | 附着各阶段实际 prepare/ready/activate/claim/complete/finalize 调用；D2 保留本包协议闭包，D6 不绕授权 |
| `skills/design-discussion/scripts/discussion_protocol.py:17–114` | eagerly 导入命令表、control、state、handoffs、phase_runs、dependencies；D2 不能仅拷 CLI |
| `skills/design-discussion/scripts/discussion_core/workflow_control.py:12–22` | 按 parents[3] 加载 guided-implementation 的 control/Git，sys.modules 注入；D3 删除兄弟定位 |
| `skills/guided-implementation/scripts/workflow_control_git.py:17,104` | 导入 control，实际调用同目录 supervision verify；D2 传递闭包/D3 同包路径 |
| `skills/guided-implementation/scripts/workflow.py:_new_state,_validate_record,_stage_prompt` | version=1，固定兄弟 Stage 2/4 和本 Stage 3 入口，run lock 位于记录旁；D5 新身份/v2/旧记录拒绝 |
| `skills/design-discussion/scripts/discussion_core/state.py:_coordination_root,_project_lock_name,_flock_with_timeout` | Git common dir 下 cc-switch/design-discussion/v1 或非 Git 项目 .codex/design-discussion/v1，锁 5 秒；D7 不按包分裂 |
| `skills/guided-implementation/scripts/supervision_protocol.py:23,793,918` | common dir 内 cc-switch-worktree-publication.lock，planning/complete 共享；D7 保留 |
| `scripts/check-dependencies.sh` | 全部五个入口和全部 Matt 同时要求；源码存在视为满足，固定全局根遗漏项目 active 注册；D4 区分 active 与 filesystem |
| `scripts/validate.sh`、`scripts/validate_repository.py` 的 test/reference discovery | 当前假定源码即 skills、测试与脚本同目录，未验单包脱离仓库；D1/D8/T1–12 |
| `README.md` | 当前 Git 仓库直接安装与五包同版说明；D1/D7 与发布步骤同步替换 |

实施应先用资源清单对全部源文件做一次引用/import inventory，核对以上类别的每个实例；证据表列出生产链而非把有限行号当作穷尽扫描。构建的未声明资源失败门槛负责防止遗漏。既有测试迁移只改定位、公共调用方式和本次新增行为，不能删除原权限、竞态或恢复覆盖来使产物测试通过。

### 工单与验收追踪

按 ask-matt 的多会话构建分支，本任务跨构建、运行时、五个阶段和兼容恢复，值得拆 Tickets。使用 to-tickets 的 expand–migrate–contract 例外：先提供可验证生成面，再迁移独立阶段，再去掉旧来源；每片都有可观察结果。规划阶段曾按用户授权完整成稿并统一评审；其后实现与归档已分别获授权。

| 顺序 | 交付切片 | 阻塞 | 决策 / 验收 |
| --- | --- | --- | --- |
| 01 | 生成并隔离运行一个自包含讨论包 | 无 | D1–3,7；AC1–3,6；T1–5 |
| 02 | 当前动作依赖预检与合法链接识别 | 01 | D4–5；AC4；T6–7 |
| 03 | 五阶段完整自包含发布面与单阶段完成 | 01,02 | D1–4,6；AC1–4,6；T2–8 |
| 04 | 跨阶段固定身份、兼容性和连续恢复 | 02,03 | D5–7；AC5–6；T8–11 |
| 05 | 发布收敛、全矩阵与现场验收说明 | 03,04 | D1–8；AC1–7；T1–14 |

01–04 可在同一集成分支分批提交，过渡阶段生成目录不得成为第二个仓库可发现入口（临时构建仅在仓库外）；最终 05 将唯一发布面落到约定位置，删除旧源入口，保持所有五个用户安装路径。每片允许在仓库外验证待生成包；最终才宣称五个安装包整体交付。五片实现与自动验证均已完成；真实宿主现场证据仍以归档记录中的明确边界为准。


## 归档记录

- 接受实现：`e8f9a55309766d8436696957c9451e9f62f8940a`；Standards、Spec 两轴 accepted，无未解决发现。
- 自动验证：`bash scripts/validate.sh` 的 368 tests 全通过（322.907s），五包 quick validation、仓库验证、构建漂移检查、compileall、diff/scope 检查通过。T1–T12 对应自动行为由源测试覆盖；fake executor 只证明 runner 合同，不证明真实 Agent 行为。
- 安装器：`skills@1.6.0` 实际发现恰好五包，临时项目逐包安装后摘要一致；未修改用户安装或 manager 私有状态。
- AC1/AC2：五个唯一入口、单包资源闭包、确定性构建已交付；AC3：脚本/Git/ledger 隔离行为已验证，T13/T14 现场部分未验证；AC4：当前动作 registry 与链接诊断已覆盖；AC5：兼容键、连续缺项、固定身份与恢复已覆盖；AC6：共享状态/权限及 Stage 3 保留、Stage 4 发布边界保留；AC7：开发、安装、升级、恢复与现场步骤已交付。
- runner v2 还固定 `registry_input` 证据文件，每次 executor launch 前重读当前 registry、resume 重验剩余目标。控制器必须刷新当前证据；`--registry-input` 可在 resume 时指定新证据文件，不改变原包身份。v1 仅原运行时恢复。
- T13/T14 尚未执行。`python3 scripts/field_acceptance.py` 生成真实宿主单包、组合、附着讨论及故障恢复步骤和证据表；这项未验证事实不被自动测试或本次归档抹去。
- 安装同步仅提供先记录旧身份、保留不可变旧版、获授权后切换、刷新并读回的步骤；本次未执行同步、远程写入或 push。

本轮日志留存于执行任务：`/tmp/independent-skills-review-validation.log`、`/tmp/workflow-installer-evidence.json`、`/tmp/workflow-field-acceptance.json`。这些是本次临时证据位置，不是运行时依赖；可用仓库上述脚本重新生成验证和现场表。
