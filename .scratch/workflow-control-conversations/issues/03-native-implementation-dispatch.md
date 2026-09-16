# 03 — 单一原生调度者完成实现与故障恢复

**What to build:** 总控只派一个原生实现调度者，按文件边界并行或串行执行，统一集成测试提交，再由总控独立审查；中断恢复不重做已验收工作。

**Blocked by:** 02 — 验证接管后归档并支持明确阶段路线。

Status: ready-for-agent
Lifecycle: completed

Spec依据：I5、I6的stage3载体；需求D7/D8；测试T6/T7及T10的stage3。依赖02的入口和接管；采用Control生产调用、临时Git、原生适配trace，保留完整implement/TDD和两轴审查。

- [x] 交互/scripted均只派1个dispatcher，不创建可见实现任务；真正验证相同Flow Worktree。
- [x] 执行分配精确路径；无重叠才并行，共享文件串行；越界或执行者Git写入计划被拒绝。
- [x] 所有写者停止后由dispatcher统一diff验收、测试和提交；总控独立审查相同候选。
- [x] 检查点覆盖执行身份、文件、完成hash和未完成项；旧写者未停禁止替换。
- [x] 恢复不重做已验收工作，未验收重验；串行降级/取消保留工作树，无自动reset或边界扩大。
- [x] runner返回完整handoff并停止本阶段，continue恢复同session；交付角色协议待归档更新。
