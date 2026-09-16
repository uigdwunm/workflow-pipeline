# 01 — 总控准备并交付专用0/1任务

**What to build:** 用户先看到唯一草案和组合创建计划，拒绝则在当前任务继续；确认只创建一个专用载体，保留总控身份及单一写权。子话题只创建独立总控，关闭门禁时仅接收。

**Blocked by:** None — can start immediately.

Status: ready-for-agent
Lifecycle: completed

Spec依据：I1–I3；需求D1–D3/D10；测试T1–T3。以Workflow Control公开JSON入口、讨论registry/handler真实调用和ledger为seam，只替换外部任务工具。此切片建立后续交接共用的控制结构，不建设独立状态存储。

- [x] 创建前准备完成且无create调用；阶段/话题拒绝、修改、取消及专用非递归分支均可观察。
- [x] dedicated-stage保留唯一controller binding，并按operation allowlist转交DW/checkpoint写权，拒绝carrier控制动作和总控并发文档写。
- [x] child只创建1个总控，父关联/顺序/强依赖正确；closed gate只读且后续用户触发才继续。
- [x] 真实/pending/不确定/失败/取消/迟到创建不会重复或授予错误身份。
- [x] 代码、测试及逐文件待归档文档一起形成完整可验证行为；保留原生文档提交边界。
