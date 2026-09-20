# 01 — 从 detached 来源交付冻结需求并进入阶段 2

**What to build:** 阶段 1 执行者在已确认目标完成有界 Markdown 交付；来源可以是独立或 detached 工作区。普通路径的阶段完成与后继启动消费真实交付证据，无关用户改动保留，source-on-target 直接验证。

Blocked by: None — can start immediately
Status: ready-for-agent

实施依据：本特性 PRD 的 D1–D3、D5–D7；冻结需求验收 1–4、7。先完成真实 Git/CLI → C → producer → B → Flow 的 T1 红/绿，不能用只调新函数的测试替代生产调用链。复用已有 entry/freeze/dispatch；新增窄生产者和原 checkpoint delivery 交易的正常路径，不修改来源历史。

- [ ] 当前阶段 1 身份、来源、目标分支、授权及 owned 文档范围在写入前固定并机械验证。
- [ ] detached 来源生成单父、仅含约定 Markdown 的交付提交；任意来源其他提交/文件不会进入目标。
- [ ] B 使用真实目标进行目标侧验证，保留 source_commit/delivery_commit；既不合并来源进目标，也不合并目标回来源。
- [ ] source-on-target 与合法已有 proof 不产生空提交；相同字节但无可信证明明确阻塞。
- [ ] 保留无关 staged/unstaged/untracked 工作；冲突、partial staging、symlink、路径/模式/身份错误在写入前拒绝。
- [ ] 普通完成 footer 与 Stage 2 入口只消费已验证交付；freeze-only 输出“需求已确认，交付待解决”。
- [ ] 从目标创建 Flow 后再次验证需求与 proof；T1–T4 和正常 T10/T11 通过。
- [ ] 新 CLI/引用进入构建资源闭包，重建生成包并执行针对性验证。

恢复分支在本切片中至少安全失败并保留 intent/有限证据；完整自动对账由 02 完成。正常路径不可通过关闭 hooks/filters 或忽略失败取得成功。
