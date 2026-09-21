# 02 — 在保留宿主时回答、暂停并停止嵌套角色

Status: ready-for-agent

**What to build:** 用户可以向活跃 runner 提交精确答案、请求暂停、恢复或取消，唯一 owner 在原 carrier 上执行；嵌套 Execution Agents 和独立审查者都停止后才完成停止状态。依据 Spec D3/D4/D5、A2/A4 和答案并发/权限测试。

**Blocked by:** 01 — 原生角色等待期间保留同一前台宿主

- [ ] RunLock 始终只有一个 owner；并发 resume 只在原 checkpoint 排队精确命令，返回 queued 不冒充已消费，不启动第二个 executor。
- [ ] 相同答案 ACK，冲突/过时答案拒绝；queued 后 owner 丢失保留请求；关闭与请求竞争不存在双消费或 silent loss。
- [ ] pause/needs_input 保留前台宿主；单纯答案不解除 pause，明确 resume 才按 C 原流程消费 deferred 决策；cancel 优先且不可由答案解除。
- [ ] 停止目标由原 dispatch、allocations/control executions 及两个固定 reviewer 活动槽推导；审查槽仅保存实际候选、axis、意图、回执，不创建通用子树 registry。
- [ ] 根 stopped、process exit 或一次 C idle 都不能替代完整停止屏障；未知创建、迟到 ref、后代活跃、未解决 continue 必须保持 pausing/cancelling。
- [ ] 停写与对账优先在原父角色的治理链完成；原父不可响应且 exact-ref 能力不足时明确阻塞，不冒充父 actor 或自动替代。
- [ ] 公共 runner/C 测试覆盖 dispatcher+execution+双审查、停止顺序颠倒、cancel 覆盖 pause、server approval 精确问答与无额外批准。
- [ ] 生产接口下确定性覆盖嵌套查停；真实嵌套集成按新冻结需求标为外部依赖待验证，不再重复调查/实验且不阻断本仓交付。单层真实查停与产品适配链验证仍必须完成；不把替身证据当宿主通过。

## Comments

由原生 to-tickets 行为生成；连续模式下粒度与阻塞关系已经按 Spec 中的切片决定确认。完成实现后才记录 Lifecycle: completed。

来源采用 Spec 的新冻结需求 commit `906efc37037c11d4a8b5ee99eac301079368fb8e`。实施遵循 Spec Further Notes 的 allowlist；运行时模板/协议及生成包属于实现候选，不能推迟到归档修补。
