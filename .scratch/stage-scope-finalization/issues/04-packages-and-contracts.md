# 04 — 验收五包与跨入口完整契约

Status: ready-for-agent
Review: tickets-review-20260926-01 — accepted
Blocked by: 02, 03
Spec: ../PRD.md — D6、D7、精确实现/归档路径及 Tickets 追溯表；Testing Decisions
Requirement: ../../../docs/requirements/2026-09-26-stage-scope-finalization.md

**What to build:** 用户通过任一合法入口使用生成 Skill 包时，正常范围定稿、受控修订和旧接管遵循同一契约，包内资源自包含，文档不会再指导提前冻结未知范围或把规划接受当成执行就绪。完成全流程组合验证，为后续真实实施交付提供可核验结果；不安装或发布版本。

前面三个切片必须已带各自必要协议、调用者、生成物与行为测试，本 Ticket 不替它们补一个尚未工作的水平层。这里处理完整发行面交叉检查、剩余跨流程说明和组合边界，所有切片沿用同一集成工作区。

- [ ] 五个包采用相同新 compatibility key：progress-v13、transfer-v8、control key 7/schema 5、runner outer 7；保持 Spec 明定的其他键，普通混合版本交换拒绝。
- [ ] stage_scope 在构建配置显式声明并进入五包资源闭包；只维护源码和构建配置，生成面由 builder 产生，源/输出差异都在 Spec 146 个实现路径内。
- [ ] CONTEXT、阶段入口、Stage transfer/progression/control 协议和 solution-design 模板完整同步 manifest、发布前封存、readiness、修订及窄恢复语义，不制造另一条授权路径。
- [ ] 确认四个其他包包含的 conversation-source 生成副本，以及 requirement-delivery 控制 fixture 的必要关联更新，没有遗漏精确路径。
- [ ] 正常 standalone/continuous/runner/attached 路径走通；完整清单正常派发，空归档合法，冻结需求和规划保护保持。
- [ ] 旧接管消费之后进入普通 v13，再使用 02 的受控修订；旧接受/包 pin 不变，当前 scope revision 正确，不能重消费旧补充授权。
- [ ] 所有 accepted/readiness/Phase 完成顺序无循环；暂停、未知调用、未完成事务、target/source/pin 漂移继续阻断派发。
- [ ] 包级独立加载与闭包校验、混合键拒绝、旧普通入口拒绝和白名单恢复均有可观察测试；不得把静态文案匹配当运行证据。
- [ ] 运行仓库 validator、构建检查和完整 packages/runtime 测试；失败只修本范围，记录实际结果和未验证部分，不谎报现场宿主/安装验收。
- [ ] 实际 diff 清单与 Spec 逐文件集合及角色所有权一致；新漏项先走 Controller 合法范围处理，不能用全仓/目录通配、直接重算原授权或修改旧 checkpoint 绕过。

**测试 seam：** 使用前面 Ticket 的生产协议集成 fixture，增加五包构建/注册兼容、foreground 与 attached 组合。最高 seam 仍为真实 C/B/Git/ledger；宿主替身只提供实际协议响应。临时构建比较生成输出，不运行 Mac 部署器、不更新注册或安装。

**完成边界：** 得到完整、可复核的实现测试及生成闭包证据；归档六个规划状态文件仍由 Closure Agent 独占。当前任务只交付规划，六项旧机械 ceiling 缺口必须在真正进入 Stage 3 前由原 Controller 解决，本 Ticket 不授权自行实施该绕过。
