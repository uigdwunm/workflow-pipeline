"""Only unsent lifecycle lookups may defer when C owns the checkpoint."""
import json
import unittest
from unittest.mock import patch

from test_stage_scope_runner import runner
import test_workflow_progress as progress_fixture


class LifecycleSnapshotRetryTests(unittest.TestCase):
    def setUp(self):
        flow = progress_fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        self.record = flow.checkpoint
        package_root, confirmed_path = flow.runner_stage2_fixture()
        raw = json.loads(confirmed_path.read_text())
        raw['registry_input'] = str(confirmed_path.resolve())
        with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')):
            confirmed = runner.validate_confirmed(raw)
        initial = runner._new_state(confirmed)
        initial['sessions']['stage2'] = flow.request['host']['thread_id']
        initial['launch']['invocation_id'] = 'original-turn'
        initial['transport'] = {}
        runner.progression.save_outer_record(self.record, initial, expected_revision=0)
        flow.begin()
        flow.invoke('observe', flow.observation())
        self.assertEqual(flow.invoke('pause')['status'], 'pausing')
        self.assertEqual(flow.invoke('observe', flow.observation('stopped', 'result'))['status'], 'paused')
        self.state = runner.CheckpointState(runner._read_checkpoint(self.record))
        self.carrier_ref = flow.state()['control']['context']['carrier']['ref']
        self.generation = flow.state()['host']['generation']
        self.log = runner._host_events_path(self.record, self.state)

    def with_production_journal(self, scenario):
        real_host = runner.foreground_host.ForegroundHost

        def factory(configuration, cwd, journal):
            host = real_host(configuration, cwd, journal)
            host.threads['stage2'] = self.carrier_ref
            self.addCleanup(host.selector.close)
            return host

        def advance(state, record, answer, host):
            with patch.object(host, '_write') as write, \
                 patch.object(host, '_read', side_effect=lambda timeout: {
                     'id': host.request_number, 'result': {'data': [], 'nextCursor': None}}), \
                 patch.object(runner.progression, 'CHECKPOINT_LOCK_TIMEOUT', .01):
                scenario(host, write)
            return 0

        with patch.object(runner.foreground_host, 'ForegroundHost', side_effect=factory), \
             patch.object(runner, '_advance_in_host', side_effect=advance):
            self.assertEqual(runner._advance(self.state, self.record), 0)

    def poll(self, host):
        runner._live_progress(self.state, self.record, host, set())

    def test_unsent_lookup_defers_without_intent_then_syncs_once(self):
        def scenario(host, write):
            before = self.record.read_bytes()
            with patch.object(runner.progression, 'handle') as apply:
                with runner.progression.record_lock(self.record):
                    self.poll(host)
                write.assert_not_called()
                apply.assert_not_called()
                self.assertFalse(self.log.exists())
                self.assertEqual(self.record.read_bytes(), before)
                self.poll(host)
                self.assertEqual(write.call_count, 2)  # live and archived pages
                apply.assert_called_once()
                saved = runner._read_checkpoint(self.record)
                self.assertEqual(saved['transport']['snapshot_key'], ['stage2', self.generation, 0])
                self.poll(host)
                self.assertEqual(write.call_count, 2)
                self.assertEqual(apply.call_count, 1)
            events = [json.loads(line) for line in self.log.read_text().splitlines()]
            self.assertEqual(sum(event['kind'] == 'request-intent' for event in events), 2)
            self.assertEqual(sum(event['kind'] == 'request-result' for event in events), 2)
        self.with_production_journal(scenario)

    def test_sent_lookup_checkpoint_error_is_not_deferred(self):
        def scenario(host, write):
            held = []

            def sent(request):
                lock = runner.progression.record_lock(self.record)
                lock.__enter__()
                held.append(lock)

            write.side_effect = sent
            try:
                with self.assertRaises(runner.entry_prepare.PreparationError) as raised:
                    self.poll(host)
                self.assertEqual(raised.exception.code, 'checkpoint_busy')
                self.assertEqual(write.call_count, 1)
                self.assertIn(host.request_number, host.responses)
            finally:
                for lock in reversed(held):
                    lock.__exit__(None, None, None)
        self.with_production_journal(scenario)

    def test_lifecycle_apply_retries_busy_but_propagates_other_errors(self):
        def scenario(host, write):
            busy = runner.entry_prepare.PreparationError('checkpoint_busy', 'C owns checkpoint')
            with patch.object(runner.progression, 'handle', side_effect=[busy, {}]) as apply:
                self.poll(host)
                self.assertNotIn('snapshot_key', runner._read_checkpoint(self.record)['transport'])
                self.poll(host)
                self.assertEqual(apply.call_count, 2)
                self.assertEqual(write.call_count, 4)
            saved = runner._read_checkpoint(self.record)
            saved['transport'].pop('snapshot_key')
            runner.progression.save_outer_record(self.record,
                {key: value for key, value in saved.items() if key != runner.progression.KEY},
                expected_revision=saved.authority_revision)
            with patch.object(runner.progression, 'handle', side_effect=
                    runner.entry_prepare.PreparationError('invalid_checkpoint', 'invalid evidence')):
                with self.assertRaises(runner.entry_prepare.PreparationError) as raised:
                    self.poll(host)
                self.assertEqual(raised.exception.code, 'invalid_checkpoint')
        self.with_production_journal(scenario)

    def test_inflight_notification_checkpoint_error_is_not_deferred(self):
        def scenario(host, write):
            held = []
            request = {'id': 'approval-1', 'method': 'item/tool/requestUserInput', 'params': {}}

            def notification(timeout):
                lock = runner.progression.record_lock(self.record)
                lock.__enter__()
                held.append(lock)
                return request

            try:
                with patch.object(host, '_read', side_effect=notification):
                    with self.assertRaises(runner.entry_prepare.PreparationError) as raised:
                        self.poll(host)
                self.assertEqual(raised.exception.code, 'checkpoint_busy')
                self.assertEqual(write.call_count, 1)
                self.assertEqual(host.server_requests['approval-1'], request)
            finally:
                for lock in reversed(held):
                    lock.__exit__(None, None, None)
        self.with_production_journal(scenario)


if __name__ == '__main__':
    unittest.main()
