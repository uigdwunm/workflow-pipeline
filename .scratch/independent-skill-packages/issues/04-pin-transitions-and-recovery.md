# 04 — 固定跨阶段包身份并保留连续执行恢复点

**What to build:** 用户组合安装或请求连续流程时，系统按当前可用目标和兼容协议启动；缺项、升级或运行中删除包只阻断未完成动作，修复后沿原任务继续。

**Blocked by:** 02 — 按当前动作核验已注册依赖并识别合法链接；03 — 五个阶段各自完整运行并交付单阶段结果。

**Status:** implemented — archived

**Spec:** 独立安装 PRD，D5–D7；AC5、AC6；T8–T11。

- [x] 阶段入口使用有效 registry 解析并固定包根 realpath、摘要和协议键，完全移除 runner 的固定兄弟阶段路径。
- [x] 单阶段不查后继；连续请求在 PRD 指定副作用前预检剩余路线，每次交接重验；缺依赖不静默降级。
- [x] 版本不同但协议键相等可互通；键不符或旧目标缺 manifest 在 dispatch/activation 前报错；不迁移 handoff 或绕原权限。
- [x] runner record v2 保存自身及三阶段固定身份，start/resume 保持既有锁、session、handoff 与 authority；旧 v1 只读拒绝并指向原运行时，不改记录或清理。
- [x] 切换安装链接后原运行继续使用原真实根；字节变化或根消失返回明确 package 错误，不混入新代码。
- [x] 失败恢复保留 completed results、接受状态、候选、Flow Worktree 和待归档 carrier；目标未 ready/active 不提前归档或推进 phase。
- [x] 真实两包进程验证同 ledger/publication 锁和 revision 竞争；两个 runner 同 record 返回 run_busy；不新增包私有锁或状态中心。
- [x] 故障注入覆盖预检后删除/改权限/更新包、结果保存后失败、merge 后 cleanup；恢复不重复已完成阶段、提交或发布。


## 交付与验证

接受候选 `e8f9a55309766d8436696957c9451e9f62f8940a` 完成此切片；前置切片均已完成。368 项源/包测试、五包 quick validation、仓库校验与两轴审查通过。统一证据及 AC 映射见 [PRD 归档记录](../PRD.md#归档记录)。
