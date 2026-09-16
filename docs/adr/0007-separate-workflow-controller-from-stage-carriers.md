# ADR-0007: Separate the workflow controller from stage carriers

Status: Accepted

One Workflow Controller retains user decisions and acceptance for each topic. Dedicated Stage-0/1 tasks own requirement writing; Stage 2 uses a native solution designer, Stage 3 one native Implementation Dispatcher with bounded Execution Agents, and Stage 4 a native Closure Agent. This prevents recursive visible tasks and competing Git writers while retaining a stable decision and recovery owner.

The active Conversation Binding stays with the controller; dedicated-stage handoffs delegate an operation allowlist without creating another owner. Standalone calls need no discussion ledger. Existing conversation checkpoints and the foreground runner record carry execution evidence; Git owns Flow Worktree facts. No scheduler or second execution registry is added.

The controller accepts the result, verifies successor readiness and activation, then archives the old visible task. The dispatcher integrates and commits, the controller independently accepts, and the closure agent publishes and cleans within handed-off authority. ADR-0001's state separation, ADR-0005's qualified unattached implementation, and ADR-0006's requirements gates remain. This new role contract has no old-handoff migration obligation.
