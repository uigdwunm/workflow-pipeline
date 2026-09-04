"""Public CLI Ticket-07 handoff and absorption scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyHandoffCliTests(TopicDependencyScenarioTest):
    def test_ticket07_initial_dependency_prepare_failure_retries_and_binds_via_cli(self): self.run_scenario("ticket07_initial_dependency_prepare_failure_retries_and_binds_via_cli")
    def test_ticket07_duplicate_absorb_release_is_atomic_via_cli(self): self.run_scenario("ticket07_duplicate_absorb_release_is_atomic_via_cli")
    def test_ticket07_injected_absorb_release_is_atomic_via_cli(self): self.run_scenario("ticket07_injected_absorb_release_is_atomic_via_cli")
    def test_ticket07_handoff_crash_recovery_is_cli_idempotent(self): self.run_scenario("ticket07_handoff_crash_recovery_is_cli_idempotent")
