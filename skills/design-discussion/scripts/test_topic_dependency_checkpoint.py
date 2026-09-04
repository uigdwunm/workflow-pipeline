"""Public CLI Ticket-07 checkpoint, authority, GC, and migration scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyCheckpointCliTests(TopicDependencyScenarioTest):
    def test_ticket07_stale_checkpoint_release_is_rejected_via_cli(self): self.run_scenario("ticket07_stale_checkpoint_release_is_rejected_via_cli")
    def test_ticket07_stale_phase_result_release_is_rejected_via_cli(self): self.run_scenario("ticket07_stale_phase_result_release_is_rejected_via_cli")
    def test_ticket07_v1_v2_dependency_migration_is_cli_stable(self): self.run_scenario("ticket07_v1_v2_dependency_migration_is_cli_stable")
    def test_ticket07_gc_retains_active_dependency_checkpoint_basis_via_cli(self): self.run_scenario("ticket07_gc_retains_active_dependency_checkpoint_basis_via_cli")
    def test_ticket07_checkpoint_artifact_currentness_filters_and_rejects_via_cli(self): self.run_scenario("ticket07_checkpoint_artifact_currentness_filters_and_rejects_via_cli")
    def test_ticket07_phase_result_currentness_filters_and_rejects_via_cli(self): self.run_scenario("ticket07_phase_result_currentness_filters_and_rejects_via_cli")
    def test_ticket07_gc_retains_frozen_child_checkpoint_authority_via_cli(self): self.run_scenario("ticket07_gc_retains_frozen_child_checkpoint_authority_via_cli")
    def test_ticket07_gc_rejects_corrupt_live_child_authority_via_cli(self): self.run_scenario("ticket07_gc_rejects_corrupt_live_child_authority_via_cli")
    def test_ticket07_broken_checkpoint_recloses_only_direct_gates_via_cli(self): self.run_scenario("ticket07_broken_checkpoint_recloses_only_direct_gates_via_cli")
    def test_ticket07_git_checkpoint_authority_evaluates_and_stales_via_cli(self): self.run_scenario("ticket07_git_checkpoint_authority_evaluates_and_stales_via_cli")
    def test_ticket07_repaired_git_checkpoint_is_current_authority_via_cli(self): self.run_scenario("ticket07_repaired_git_checkpoint_is_current_authority_via_cli")
    def test_ticket07_invalid_latest_checkpoint_does_not_fallback_via_cli(self): self.run_scenario("ticket07_invalid_latest_checkpoint_does_not_fallback_via_cli")
    def test_ticket07_bound_same_tree_child_uses_topic_and_checkpoint_cli(self): self.run_scenario("ticket07_bound_same_tree_child_uses_topic_and_checkpoint_cli")
