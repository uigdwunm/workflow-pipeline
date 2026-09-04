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
