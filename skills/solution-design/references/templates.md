# Fixed Formats

Use these formats verbatim and replace every placeholder. Exact confirmation
means that, after trimming surrounding whitespace, the entire reply equals
`确认`. Punctuation, conditions, prefixes, suffixes, `继续`, and `可以` are not
confirmation.

## Stepwise subagent launch confirmation

```text
确认事项：启动 2方案子 agent
本次目标：<goal>
需求来源：<immutable draft path and commit/hash | current-task accepted handoff>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
方案子 agent：solution_designer
目标模型：<primary thread exact model>
模型来源：主线程
推理强度：<primary thread exact reasoning effort>
强度来源：主线程
上下文方式：fork_turns=none；不继承历史；仅读取需求来源和明确交接材料
执行环境：当前项目 local checkout
Worktree：不创建
允许动作：执行标准 $to-spec、ADR、$ask-matt、$to-tickets、原生发布；本地文档写入使用 document lease，repository lease 可用后仅提交阶段自有文档
禁止动作：修改实现代码、创建 PR、部署、发布版本、进入 3实现或处理无关任务
异常规则：执行失败、结果或副作用不确定、意外情况、与原计划不符或需要调整计划时停止并报告
流程模式：逐阶段确认
确认后结果：使用上述具体模型、强度和权限启动一个 2方案子 agent
修改方式：说明需要修改的目标、需求来源、项目、载体、模型、强度或权限
确认方式：回复 `确认`
```

## Continuous automatic launch disclosure

This must be the final user-visible commentary immediately before
`spawn_agent`.

```text
连续模式自动动作：启动 2方案子 agent
本次目标：<goal>
需求来源：<immutable draft path and commit/hash | current-task accepted handoff>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
方案子 agent：solution_designer
目标模型：<primary thread exact model>
模型来源：主线程
推理强度：<primary thread exact reasoning effort>
强度来源：主线程
上下文方式：fork_turns=none；不继承历史；仅读取需求来源和明确交接材料
执行环境：当前项目 local checkout
Worktree：不创建
允许动作：执行标准 $to-spec、ADR、$ask-matt、$to-tickets、原生发布；本地文档写入使用 document lease，repository lease 可用后仅提交阶段自有文档
禁止动作：修改实现代码、创建 PR、部署、发布版本、进入 3实现或处理无关任务
异常规则：执行失败、结果或副作用不确定、意外情况、与原计划不符或需要调整计划时停止并报告
流程模式：连续执行后续全部流程
执行状态：本条披露后立即启动；不等待阶段确认
```

## Exact child bootstrap prompt

```text
$solution-design

协议：solution-design-subagent-v1
角色：你是本次 2方案 的唯一阶段负责人；主 agent只负责用户决策和阶段编排。
本次目标：<goal>
需求来源类型：<immutable-problem-framing-draft | current-task-handoff>
需求草案：<absolute path | none>
草案提交：<commit | none>
草案 SHA-256：<hash | none>
当前任务交接：<complete accepted target-relevant context | none>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
任务设置：model=<exact model>; reasoning_effort=<exact effort>; fork_turns=none; worktree=none
流程模式：<逐阶段确认 | 连续执行后续全部流程>
允许动作：完整执行 $to-spec、必要 ADR、$ask-matt、$to-tickets、精确目标原生发布；本地文档写入使用 document lease，repository lease 可用后仅提交阶段自有文档。
禁止动作：修改实现代码、创建 PR、部署、发布版本、改变规划目标、进入 3实现、修改 1拷问草案或处理无关任务。
文档权威：1拷问草案拥有需求；CONTEXT.md 拥有术语；ADR 拥有难逆决策；Spec 拥有实施方案与测试决策；Tickets 拥有实施切片和阻塞关系。不要创建单独的 2方案草案。
逐阶段模式：在方案审阅和 Tickets 审阅点返回固定 review 消息并停止，收到匹配 ID 的 PARENT_DECISION 后继续。
连续模式：此前的 执行后续全部流程 是标准方案选择和原生审阅问题的预授权；不要发 review 消息，不运行额外质量门，直接选择、发布并交付。
异常：执行失败、结果不确定、意外情况、与需求或已发布计划不符、或者需要调整计划时，返回 SOLUTION_DESIGN_ANOMALY 并停止；不要擅自调整。
启动：第一条状态必须使用 SOLUTION_DESIGN_STARTED。
完成：只在阶段工作完成后返回一次 SOLUTION_DESIGN_COMPLETE；不要自行进入 3实现。
协议文件：完整遵循 $solution-design 的 references/subagent-protocol.md 和 references/templates.md。
```

## Started status

```text
SOLUTION_DESIGN_STARTED
协议：solution-design-subagent-v1
本次目标：<goal>
需求来源：<source>
规划载体：<exact target>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
```

## Solution review

```text
SOLUTION_REVIEW_REQUIRED
协议：solution-design-subagent-v1
审阅 ID：<unique id>
需求来源：<source>
Spec 草稿：<path or URL>
方案摘要：<summary>
关键决策：<decisions>
ADR：<paths or none>
实现范围：<scope>
规划载体：<exact target>
已完成：<work that will not repeat>
恢复位置：<checkpoint>
确认事项：接受当前方案并由同一个子 agent发布 Spec
修改方式：说明需要修改的方案内容
确认方式：回复 `确认`
```

## Tickets review

```text
TICKETS_REVIEW_REQUIRED
协议：solution-design-subagent-v1
审阅 ID：<unique id>
Spec：<published path or URL>
Tickets 草稿：<paths or structured list>
Ticket 数量：<number>
实施顺序：<order>
依赖关系：<dependencies>
规划载体：<exact target>
已完成：<work that will not repeat>
恢复位置：<checkpoint>
确认事项：接受当前 Tickets 并由同一个子 agent发布
修改方式：说明需要增加、删除、合并、拆分或修改的 Ticket
确认方式：回复 `确认`
```

## Parent decision

```text
PARENT_DECISION
协议：solution-design-subagent-v1
目标子 agent：<trusted agent id>
关联类型：<solution-review | tickets-review | anomaly>
关联 ID：<review or anomaly id>
用户决定：<exact confirmation or requested change>
恢复位置：<checkpoint>
```

## Anomaly

```text
SOLUTION_DESIGN_ANOMALY
协议：solution-design-subagent-v1
异常 ID：<unique id>
异常类型：<执行失败 | 结果不确定 | 意外情况 | 与原计划不符 | 需要调整计划>
当前阶段：2方案
原计划：<baseline>
实际情况：<observed state>
偏差：<exact mismatch>
影响：<scope, behavior, delivery, risk, or side-effect impact>
建议：<recommended response>
其他选项：<reasonable alternatives>
已完成：<work that will not repeat>
尚未执行：<stopped actions>
现有文件与发布：<paths, URLs, IDs and states>
恢复位置：<checkpoint>
确认事项：按上述建议处理异常并继续
修改方式：说明要选择的其他处理方式
确认方式：回复 `确认`
```

## Completion

```text
SOLUTION_DESIGN_COMPLETE
协议：solution-design-subagent-v1
需求来源：<draft path, commit and hash | current-task handoff>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<repository identity>
规划载体：<exact target>
Spec：<path or URL>
ADR：<paths or none>
Tickets：<paths or URLs | none>
规划提交：<commit | none>
发布结果：<completed results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<confirmed Testing Decisions or exact Spec section>
Matt 原生发布：Spec=<result>; Tickets=<result or none>; 原生标签与阻塞关系=<results or not applicable>
阶段授权：2方案标准原生动作已完成；授权不延续至 3实现或 4归档
扩展远程操作：<未授权且未执行 | exact separately authorized actions and results>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
工作区状态：implementation paths and index clean; stage-owned planning paths committed; unrelated documentation paths listed and unstaged
```

## Stepwise success footer

```text
阶段结果：方案完成
Spec：<clickable path or URL>
ADR：<clickable paths or none>
Tickets：<clickable paths or URLs | 不需要，完整 Spec 可在一个实现上下文中完成>
目标仓库：<verified absolute path>
规划提交：<commit | none>
发布结果：<results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<confirmed Testing Decisions or exact Spec section>
规划载体：<exact local convention, GitHub owner/repository, or tracker target>
Matt 原生发布：Spec=<verified path or URL>; Tickets=<verified paths or URLs, or none after native decision>; 原生标签与阻塞关系=<results or not applicable>
阶段授权：已用于 2方案标准原生动作；不延续至 3实现或 4归档
扩展远程操作：<未授权且未执行 | exact separately authorized actions and results>
工作区状态：实现区与暂存区干净；本阶段规划文档已提交；其它文档改动已列出且未暂存
流程模式：逐阶段确认
下一阶段：`$guided-implementation`（3实现）
进入条件：已满足
确认事项：进入 3实现
确认方式：回复 `确认`
连续模式：回复 `执行后续全部流程`，自动执行 3实现和 4归档
```

## Continuous completion handoff

Emit this complete block and invoke `$guided-implementation` in the same turn.
Every value comes from the trusted child's structurally complete terminal
message; do not independently revalidate the reported facts.

```text
阶段结果：方案完成
需求来源：<draft path, commit and hash | current-task handoff>
Spec：<clickable path or URL>
ADR：<clickable paths or none>
Tickets：<clickable paths or URLs | 不需要，完整 Spec 可在一个实现上下文中完成>
目标项目：projectId=<id or none>; path=<absolute path>
目标仓库：<absolute repository path>
规划提交：<commit | none>
发布结果：<child-reported results>
实现依据：<Spec and Tickets | complete Spec>
测试依据：<child-reported Testing Decisions or exact Spec section>
规划载体：<exact local convention, GitHub owner/repository, or tracker target>
Matt 原生发布：Spec=<child-reported path or URL>; Tickets=<child-reported paths or URLs, or none>; 原生标签与阻塞关系=<child-reported results or not applicable>
阶段授权：已用于 2方案标准原生动作；不延续至 3实现或 4归档
扩展远程操作：<未授权且未执行 | exact separately authorized actions and child-reported results>
工作区状态：实现区与暂存区干净；本阶段规划文档已提交；其它文档改动已列出且未暂存
完成依据：可信 solution_designer 的结构完整终态消息；连续模式未独立复查实际状态
流程模式：连续执行后续全部流程
下一阶段：`$guided-implementation`（3实现）
进入条件：已满足
执行状态：本条交接后同一轮立即进入 3实现；不等待阶段确认
```

## Pre-launch anomaly

```text
流程异常：需要用户决策
异常类型：2方案子 agent启动前异常
当前阶段：2方案
本次目标：<goal>
需求来源：<source>
目标项目：<project>
目标仓库：<repository>
规划载体：<target>
待解析设置：model=<value or unavailable>; reasoning_effort=<value or unavailable>
实际情况：<missing, ambiguous, unavailable, or mismatched fact>
影响：无法生成完整启动披露或精确启动 solution_designer
建议：<recommended correction or recovery>
确认事项：按上述建议修正启动信息
修改方式：说明要采用的其他值或处理方式
确认方式：回复 `确认`
```

## Child termination anomaly

```text
流程异常：需要用户决策
异常类型：子 agent异常终止
当前阶段：2方案
可信子 agent：<agent id>
已完成：<known results>
未完成：<remaining work>
现有文件与发布：<paths, URLs, IDs and states | none>
建议：优先恢复同一个子 agent；无法恢复时再确认是否创建替代子 agent
恢复位置：<checkpoint>
确认事项：按上述建议恢复
确认方式：回复 `确认`
```
