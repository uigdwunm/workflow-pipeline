# 01 — 从单一源码生成可独立运行的讨论包

**What to build:** 用户只获得生成的 design-discussion 包，就能在无源码和兄弟 Skill 的环境启动真实讨论协议、操作临时 ledger 并验证 Git 交付；维护者修改共享源码后可重复生成同样的包。以此验证共享运行闭包与构建方案，暂不改变所有安装入口。

**Blocked by:** None — can start immediately.

**Status:** implemented — archived

**Spec:** 独立安装 PRD，D1–D3、D7；AC1–AC3、AC6；T1–T5。

- [x] 建立不可发现的源模板、显式五包资源清单格式、共享源 owner 和确定性构建器；过渡产物只生成到仓库外，仓库不增加重复入口。
- [x] 讨论包只有一个用户入口，包含完整 discussion→control/Git→supervision 及所需 settings 闭包；无 Matt、其他阶段入口、测试文件或越界链接。
- [x] 消除包内运行的兄弟 Skill 导入和全局模块注入；用本包 CLI 从任意 CWD 启动，保留原 JSON、权限与错误合同。
- [x] 临时环境真实执行 bootstrap、read、至少一次授权文档操作及 Git 证据校验；没有源码路径和 PYTHONPATH 仍成功。
- [x] 两次构建字节/权限一致；共享源变更可传播；删除资源、错误 import/链接、手改副本均被 check 拦截。
- [x] 迁移/适配相关原有测试并保持权限、恢复和 revision 覆盖；不把 --help 当作行为验证。

**实施边界：** 本片允许对宽重构采用 expand，源迁移先保留未迁移调用者可运行，最终 contract 由 05 负责。构建副本不成为第二套手工源码。


## 交付与验证

接受候选 `e8f9a55309766d8436696957c9451e9f62f8940a` 完成此切片；前置切片均已完成。368 项源/包测试、五包 quick validation、仓库校验与两轴审查通过。统一证据及 AC 映射见 [PRD 归档记录](../PRD.md#归档记录)。
