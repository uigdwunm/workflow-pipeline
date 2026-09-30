# 启动校验职责梳理

> 历史阶段记录：保留当时的诊断与实验结论。正式实现已完成，最新验收见 [RESULT.md](RESULT.md)。

本记录回答启动时哪些校验必须保留、谁负责、哪些结果可在一次操作中复用。它是职责分析和调整方向，不是已完成的重构方案。运行时代码未修改。

## 源码与证据范围

源码分析固定在提交 `8d5e8eae7e458cc19ea3f021129a449699fd7fce`，通过 `git show` 阅读。工作区另有任务的变更，不纳入此责任分析。计数来自 `ProgressTests.test_launch_is_saved_before_host_and_never_reissued` 的单用例采样，详见 `INVESTIGATION.md` 和 `profile-summary.json`。它覆盖初次独立 Stage 2 启动，不代表所有阶段、恢复与附着讨论路径。

这里的 A、B、C 是软件 Module：A 为入口准备，B 为交接与派发，C 为进度和事务。实际 Controller、方案 Agent、Dispatcher 等角色的交接是另外的信任边界，仍需在接收端验证。

## 现有启动顺序

1. C 的 `handle()` 锁定并读取自己的进度记录，核验版本、revision、已有状态和未决操作。
2. C 的 `start()` 检查前阶段、宿主、来源、原授权及 checkpoint 位置；调用 A `entry.resolve()` 重新核对实际入口。
3. C 调 B `stage_handoff.prepare()`；B 又通过 `refresh()` 调 A，读取来源、scope、Flow 绑定、交付、配置和前驱，生成封存输入。
4. C 保存状态和 B 事务请求，调用 `stage_dispatch.prepared()`。B 调 `handoff.verify()`；该方法重跑 `prepare()`，然后检查 control 的 owner、stage 和 requirement。
5. B 的 checkpoint 转换验证动作所需的当前事实，执行授权或预留状态变更，返回 dispatch 和 control 结果。
6. C 保存并消费 B 结果，准备进入 launch。C 的 `verify_launch()` 再调 `handoff.verify()`，再次重跑 `prepare()`。
7. C 保存 `invoke-host` 动作并返回指令。外部宿主的实际调用、认证和去重由相应 Adapter 执行，不能把返回指令当成宿主已执行。

对应源码锚点均按上述基线：`workflow_progress.py:1263,1269,1358,1387,2109,2115,2207,3761`；`stage_handoff.py:476,482,677`；`stage_dispatch.py:34,115,117`；`workflow_control_git.py:283,350`。

主体只有一次 start，却出现三次 prepare、两次 verify。215 次 Git 调用集中在 start。两次 verify 参数相同，但中间发生了授权或预留状态变更；这不能证明它们可整次删除。

## 校验类别和责任

| 校验内容 | 应负责的 Module | 何时需要检查 | 本次操作内如何复用 |
| --- | --- | --- | --- |
| 请求字段、协议版本、stage 与 role、纯配置结构、集合关系 | 公开输入入口及 B 的输入规范化 | 新外部输入、重新加载的记录、独立 B CLI 请求 | 同一可信调用链复用规范化结果；改变输入或开始新请求后重新验证 |
| 固定 commit 对象的 mode、内容及文档 hash | B 的来源与交付验证 | 绑定来源时、换来源或仓库时、恢复后重新建立本次上下文时 | 按明确仓库、完整 commit、路径在本次操作内复用；当前工作区文档单独核验 |
| 实际 cwd、运行任务、注册、当前配置、包身份 | A | 进入操作、执行所需的真实身份或配置边界、恢复和接收新 Actor 时 | 紧邻的内部调用可共用该边界的解析结果；保留最晚一次外部读取，不能把早先结果永久当成当前事实 |
| Worktree 注册、common directory、分支、HEAD、祖先关系及创建身份 | Git Adapter；由动作所属 Module 请求 | 变更执行授权前、使用绑定执行动作前、Git 写入后、恢复和实际 Agent 接收时 | 同一次 Git 观察中统一收集，返回所需事实；它不是阻止其他写入者的租约 |
| 工作区源码、受保护文档、路径 alias 与 symlink、文件权限及索引 | Git 和文件系统 Adapter | 授权及执行相关动作前，文件或索引可能改变后 | 同一次观察内复用；不能按 commit 不变推断文件或索引不变 |
| Controller、scope authority、Phase Run、前驱 readiness、允许路径 | 权威 control 或讨论账本 Module | 使用权限、转换权限、恢复及跨阶段时 | 由该权威 Module 核验；C 的进度锁和旧 envelope 不能代替当前权威 |
| reservation、expected revision、幂等键和未知 B 结局 | B checkpoint 与 C 事务管理 | 转换时；保存结果、消费或恢复时 | 保留原请求和结果；已有结果直接消费。未知结果按原事务恢复，不用新身份重复执行 |
| 停止意图、宿主 instance 与 generation、未决 action、实际调用证明 | C 和可信宿主 Adapter | 发出或实际执行动作前、宿主响应和恢复时 | 只复用当前 action 对应的真实证明；重复响应 ACK 不制造新 invocation |

scope 校验需要拆清两种工作：路径字符串规范和集合交集属于数据校验；当前路径的 symlink、alias、内容及生成资源闭包依赖文件系统。后者不能被“已解析 scope”一并跳过。

## 可确认的结构问题

### verify 把数据准备与实时有效性混在一起

`stage_handoff.verify()` 通过重新执行 `prepare()` 并比较整个结果来验证输入。prepare 同时包含字段检查、配置投影、当前入口读取、源文件检查、Git 绑定和交付检查。因此某个消费者只需确认当前授权或关键状态时，也支付了整套准备的成本。

调整方向是分别表达“输入数据有效”“权威允许此次转换”“当前现实仍允许此次动作”，让每道真实关口执行完整的必要条件，不重复其无关准备工作。

### C 与 B 在准备入口重复解析 A

C1263 和 B482 都重读实际入口。它们是同一个启动操作中的合并候选，但不能简单复用更早的读取。合并需保留该边界最晚的一次外部观察，并保持 checkpoint 安全性、runner 确认范围、包 pin、错误处理和来源绑定。

可以通过私有 Python 调用返回本次解析事实及规范化输入。公开 JSON 与独立 CLI 入口仍须完整验证；不能增加可由输入伪造的 `already_verified` 标志。

### 记录结构检查同时读取外部状态

`workflow_progress.validate_state()` 在记录结构检查中执行包身份核验及 `stage_scope.validate_scope()`，后者读 Git 和文件系统。请求入口先调用这个方法，普通 advance、observe 和重复 observe 因此各有四次 Git 读取。

应区分记录结构有效性和执行有效性。不能直接删除当前入口的外部检查：包身份保护、权限变化、停止或未知宿主状态的输入优先级，以及任何对“当前就绪”的声明都要保留。显示已保存状态与声称当前可执行也要明确区分。

### 绑定校验没有向上提供查出的事实

底层 `supervision_protocol.verify_worktree()` 返回规范化 binding 和当前 commit，但 `workflow_control_git.verify_binding()` 只看子进程退出码并返回 None。结果中的事实无法供当前动作的后续检查使用。

应在本次观察内提供经过边界验证的事实及失败上下文。它只能减少紧邻检查的重新读取，不能证明未来 Git、权限或宿主不会变化。跨包加载、固定运行时身份和 CLI 独立性仍需保留。

## 推荐的职责顺序

1. 公开入口验证请求和重读的记录，明确本次实际 Actor、原版本和权威来源。
2. B 形成一次规范化准备数据，并绑定固定来源和输入语义；紧邻内部调用使用这份数据。
3. 权威转换前取得该动作所需的当前事实，核验权限，再执行原 checkpoint 转换。
4. C 持久化原 B 请求、结果和最新 control；原事务的身份、幂等与未知结局规则保持。
5. 发出宿主动作前重新核验变化敏感条件与自己的有效预留，保留停止优先级和唯一 action。
6. 实际宿主与新 Actor 在自己的真实接收边界认证、去重并核验执行所需事实。

这是“一次规范化准备加真实关口复验”的方向，不承诺只访问 Git 一次。两个实时关口仍然存在，查询可以在各自关口内部统一收集。校验对象及阶段差异需逐项保持，不能统一成一个始终相信旧输入的缓存。

## 附着讨论与恢复例外

专用讨论路径的 reserve-launch 会改变账本控制视图。当前 `verify_launch()` 使用 post-mutation verifier，并要求账本读回恰好等于自己的 reservation；用 mutation 前的完整结果机械比较会错误拒绝合法变化，复用它又会遗漏预留被覆盖。

跨请求、CLI、锁释放、恢复和真实 Actor 交接后，本次进程内验证结果失效。C 的记录锁只保护自己的 JSON 记录，不锁 Git、包文件、宿主或讨论账本。恢复发现已有 transaction_result 时，消费原结果；恢复到 launch 时仍要重新验证现实条件。

Stage 3 的实现验证继续依据封存基线，Stage 4 才处理当前目标提交。调整校验不能把与动作无关的实时 main 比较重新塞回 Stage 3。

## 后续重构必须保护的场景

- 准备后 HEAD、索引、受保护文件、路径 alias 或 symlink 改变。
- Worktree 被换绑、注销，创建身份或 common directory 改变。
- 原路径包内容变化、注册变更、运行配置改变或保留的原运行时不再匹配。
- Scope authority、Phase Run 或 readiness 被更新或取消；载入旧版本记录时继续拒绝错误运行时。
- B 预留被其他账本操作覆盖、原 revision 或幂等键不匹配。
- B 已生效但 C 尚未保存结果，以及恢复时不得重复派发的未知结局。
- 授权后收到停止或未知宿主状态，宿主重启或 generation 改变。
- 外部 JSON 伪造已验证声明；重复内容不得被包装成新的真实调用证明。

下一步应先设计这三个验证职责的内部接口和负面回归，再测量 start 的调用数。现有 382 至 324 的查询合并仍是可复现候选，但不代替本轮职责调整；新的次数及全量收益尚未测量。
