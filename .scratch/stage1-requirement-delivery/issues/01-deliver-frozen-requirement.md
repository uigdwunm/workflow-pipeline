# 01 — 从 detached 来源交付冻结需求并进入阶段 2

**What to build:** 阶段 1 执行者在已确认目标完成有界 Markdown 交付；来源可以是独立或 detached 工作区。普通路径的阶段完成与后继启动消费真实交付证据，无关用户改动保留，source-on-target 直接验证。

Blocked by: None — can start immediately
Status: ready-for-agent
Lifecycle: completed

实施依据：本特性 PRD 的 D1–D3、D5–D7；冻结需求验收 1–4、7。先完成真实 Git/CLI → C → producer → B → Flow 的 T1 红/绿，不能用只调新函数的测试替代生产调用链。复用已有 entry/freeze/dispatch；新增窄生产者和原 checkpoint delivery 交易的正常路径，不修改来源历史。

- [x] 当前阶段 1 身份、来源、目标分支、授权及 owned 文档范围在写入前固定并机械验证。
- [x] detached 来源生成单父、仅含约定 Markdown 的交付提交；任意来源其他提交/文件不会进入目标。
- [x] B 使用真实目标进行目标侧验证，保留 source_commit/delivery_commit；既不合并来源进目标，也不合并目标回来源。
- [x] source-on-target 与合法已有 proof 不产生空提交；相同字节但无可信证明明确阻塞。
- [x] 保留无关 staged/unstaged/untracked 工作；冲突、partial staging、symlink、路径/模式/身份错误在写入前拒绝。
- [x] 普通完成 footer 与 Stage 2 入口只消费已验证交付；freeze-only 输出“需求已确认，交付待解决”。
- [x] 从目标创建 Flow 后再次验证需求与 proof；T1–T4 和正常 T10/T11 通过。
- [x] 新 CLI/引用进入构建资源闭包，重建生成包并执行针对性验证。

恢复分支在本切片中至少安全失败并保留 intent/有限证据；完整自动对账由 02 完成。正常路径不可通过关闭 hooks/filters 或忽略失败取得成功。

## Comments

### 阶段 4 完成证据（2026-09-20）

实现候选：`2233a49b78b18f8711ec25c6d87ea750b8e4b337`。Standards 与 Spec 两轴均已接受同一候选；审查引用分别为 `/root/sg_standard_standards_b5e726d_t_7aa95e6e9616`、`/root/sg_standard_spec_b5e726d_t_b7682d6c274b`。下列勾选表示已接受实现及自动化验证完成。

验证：29 项 requirement-delivery 针对性测试通过；完整 `scripts/validate.sh` 通过，655 项测试、1 项既有跳过，含确定性构建检查、包检查和 repository validator（运行记录 `/tmp/stage1-delivery-gate-full.log`）。恢复快照 SHA-256 与提交 blob 一致，候选工作区干净且 `git diff --check` 通过。阶段 4 仅修改本 PRD 与三个 Ticket 的 Lifecycle、复选框和完成证据，复用上述代码验证结果。

验证边界：真实 Git、文件、索引和 CLI 边界已自动化验证；宿主适配使用测试替身，真实宿主任务创建、消息认证/发送、归档和 UI 未现场验证。未进行本机部署或远程写入。冻结需求与方案语义保持不变。

正常链证据：`test_detached_freeze_checkpoint_delivery_receiver_and_new_flow` 通过真实 detached 来源、C 交付、B 接收和目标 Flow 读取验证；`test_unrelated_source_history_and_target_user_work_are_preserved` 核对无关用户文件及索引保全。source-on-target、重复交付、无 proof 同字节、越界/actor 和 owned 冲突覆盖随同 29 项测试通过，生成 CLI 资源闭包随完整验证通过。
