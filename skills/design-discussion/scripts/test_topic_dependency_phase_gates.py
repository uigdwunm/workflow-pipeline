"""Public CLI Ticket-07 Phase-0/1 gate entrypoint scenarios."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from test_topic_dependency_support import TopicDependencyScenarioTest


class TopicDependencyPhaseGateCliTests(TopicDependencyScenarioTest):
    def test_ticket07_closed_gate_blocks_public_entrypoints_via_cli(self): self.run_scenario("ticket07_closed_gate_blocks_public_entrypoints_via_cli")
    def test_ticket07_closed_gate_rechecks_ready_and_activation_via_cli(self): self.run_scenario("ticket07_closed_gate_rechecks_ready_and_activation_via_cli")
    def test_ticket07_closed_gate_allows_acceptance_and_nonadvancing_cli_operations(self): self.run_scenario("ticket07_closed_gate_allows_acceptance_and_nonadvancing_cli_operations")
    def test_ticket07_gate_closure_after_activation_preserves_run_then_phase2_cutoff_via_cli(self): self.run_scenario("ticket07_gate_closure_after_activation_preserves_run_then_phase2_cutoff_via_cli")
    def test_ticket07_closed_gate_blocks_phase1_no_code_integration_via_cli(self): self.run_scenario("ticket07_closed_gate_blocks_phase1_no_code_integration_via_cli")
    def test_ticket07_nested_gate_payload_shapes_fail_stably_via_cli(self): self.run_scenario("ticket07_nested_gate_payload_shapes_fail_stably_via_cli")
