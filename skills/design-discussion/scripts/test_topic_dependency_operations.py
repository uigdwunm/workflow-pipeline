"""Public CLI Ticket-07 dependency update, selection, and invalidation scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyOperationCliTests(TopicDependencyScenarioTest):
    def test_ticket07_nested_authority_selection_is_bounded_and_sorted_via_cli(self): self.run_scenario("ticket07_nested_authority_selection_is_bounded_and_sorted_via_cli")
    def test_ticket07_gate_selection_requires_exact_closed_dependency_set_via_cli(self): self.run_scenario("ticket07_gate_selection_requires_exact_closed_dependency_set_via_cli")
    def test_ticket07_active_closed_dependency_limit_is_atomic_via_cli(self): self.run_scenario("ticket07_active_closed_dependency_limit_is_atomic_via_cli")
    def test_ticket07_corrupt_persisted_authorities_fail_closed_via_cli(self): self.run_scenario("ticket07_corrupt_persisted_authorities_fail_closed_via_cli")
    def test_ticket07_forged_child_basis_authority_mismatch_fails_closed_via_cli(self): self.run_scenario("ticket07_forged_child_basis_authority_mismatch_fails_closed_via_cli")
    def test_ticket07_concurrent_dependency_updates_commit_once_via_cli(self): self.run_scenario("ticket07_concurrent_dependency_updates_commit_once_via_cli")
    def test_ticket07_injected_dependency_update_is_atomic_via_cli(self): self.run_scenario("ticket07_injected_dependency_update_is_atomic_via_cli")
    def test_ticket07_dependency_update_reasons_keep_exact_operation_ids_via_cli(self): self.run_scenario("ticket07_dependency_update_reasons_keep_exact_operation_ids_via_cli")
    def test_ticket07_direct_release_reason_keeps_exact_operation_id_via_cli(self): self.run_scenario("ticket07_direct_release_reason_keeps_exact_operation_id_via_cli")
    def test_ticket07_reopen_reason_keeps_exact_result_and_operation_ids_via_cli(self): self.run_scenario("ticket07_reopen_reason_keeps_exact_result_and_operation_ids_via_cli")
    def test_ticket07_reopen_all_keep_recloses_result_gate_via_cli(self): self.run_scenario("ticket07_reopen_all_keep_recloses_result_gate_via_cli")
