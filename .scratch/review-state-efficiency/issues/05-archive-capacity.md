# 05 — 长运行归档、固定容量与恢复证据保护

Status: needs-info
Design status: draft — 统一审阅前不可领取
Blocked by: 01
Spec: ../PRD.md
Scope inventory: ../manifest.json（T05）

## What to build

Ticket/工作批次完成后历史退出活跃读取，同一事实和原证据只保存一次；10000个已完成片与10000次重复poll仍保持有限当前root和恢复读取。容量不足拒新动作，但原副作用回执、停止与未知调用恢复有预留路径。

## 合同和验收

- [ ] 每32片或256事件封批，事件段≤1MiB；完成且停稳subject出活跃窗口；root不存累计ID列表。
- [ ] 归档前后next_action、权限、claims有效性、原调用重放结果完全相同；日常恢复不读archive。
- [ ] receipt通过hash精确路径查，不扫描历史或加载累积索引；相同key不同digest拒绝。
- [ ] 临时24小时/孤立30天GC规则满足全部pin和两代标记；已提交event/receipt/最终claim默认保留。
- [ ] 扫描中新增引用、缺失引用、unknown、未完成事务均阻止不安全删除；取消也不意味着可删证据。
- [ ] 75%整理/90%拒新分配/硬上限拒新工作，停止恢复预留不被业务消耗；真实ENOSPC保留原intent不重发。
- [ ] 新格式幂等receipt累计磁盘受2GiB预算制约，明确背压而非无限历史承诺；单片边际物理字节在100/1000/10000片规模不得增长超过10%，不只是root变小。

## Function Execution Plan（草案）

| Step | 目标符号 | 能力、输入→输出、失败行为 | 前置 |
| --- | --- | --- | --- |
| S01 | 新 `workflow_state_store.compact`及内部分段/receipt定位/mark-sweep helpers | 已提交root+subject生命周期→归档manifest/新root；完整引用检查、256项分页、当前authority CAS；中断仅遗留可回收未提交对象 | 无 |
| S02 | `Progress.save/consume_transaction/native_finalize/finish_lifecycle/archive_action/receive_publication` | 批次/Ticket/stage/delivery里程碑→整理请求；unknown/停止撤销钉住subject，不能因“completed文本”直接归档 | S01 |
| S03 | `workflow_progress.handle`、`Progress.control_action/allocation/observe/decide` | 用store exact-key lookup替换遍历历史的duplicate判断；旧subject陌生请求拒绝；ACK需要已提交revision证明 | S01 |
| S04 | `workflow.py._atomic_save/_stage_projection/_advance_in_host`、`workflow_progress.verify_completion` | runner history/predecessor只存引用；交付核验分页读取证据链，不把全链塞回状态；容量失败保留已提交结果 | S01、S02 |

实施符号以manifest核验列表为准。S01–S04共同完成归档到恢复的纵向行为，不新建独立“垃圾回收服务”。

## Verification references

V01：新 `tests/runtime/test_state_archive.py`，以真实handle和临时磁盘预算制造1/10/100/1000/10000历史subject；断言当前/恢复字节预算、精确旧回执replay、冲突拒绝。

V02：`test_bounded_progress.py`在对象写/目录fsync/root replace/ledger提交/receipt回传各点故障；断言原外部调用一次、unknown可查、旧有效root未被半写覆盖。

V03：`test_publication_recovery.py`、`test_business_recovery.py`、`test_accepted_query_retry.py`覆盖completed/暂停/未知历史归档。GC测试用可控时钟推进资格，只测试删/保留行为，不断言私有扫描函数调用数。

经统一runner运行，不将真实旧325MB/39MB任务作为可写fixture；规模数据由新协议合成。

## Comments

待统一用户审阅，非Stage3 handoff。
