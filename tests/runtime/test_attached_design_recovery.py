"""Attached replacement retains the original Phase Run through C/B and Git."""
import unittest
from test_design_recovery import DesignRecoveryTests
from test_attached_flow_progress import AttachedFlowTests, entry
import stage_handoff


class AttachedDesignRecoveryTests(DesignRecoveryTests):
    def setUp(self):
        self.f = AttachedFlowTests(methodName='runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_recovered_designer_continues_and_delivers_under_original_phase(self):
        request = self.ready(); f = self.f
        phase = f.state()['handoff']['authorization']['phase']
        activated = f.invoke('control', request)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:replacement', activated)
        saved = f.state()['handoff']
        context = f.state()['control']['context']
        stage_handoff.phase_evidence(saved, role_ref='native:replacement', control_context=context)
        with self.assertRaises(entry.PreparationError):
            stage_handoff.phase_evidence(saved, role_ref='native:designer', control_context=context)
        with self.assertRaises(entry.PreparationError):
            stage_handoff.phase_evidence(saved, role_ref='native:unrelated', control_context=context)
        observed = f.observation('idle', 'result', {'delivery_id':'replacement-question',
            'status':'needs_input', 'payload':{'question':'confirm remaining design detail'}}, ref='native:replacement')
        pending = f.invoke('observe', observed)['pending']
        continued = f.invoke('decide', {'decision_id':pending['decision_id'], 'subject':pending['subject'],
            'answer':'continue original scope', 'reference':'controller:detail'})
        if continued['next_action']['operation'] == 'inspect-host-state':
            continued = f.invoke('observe', f.observation('idle', 'result', ref='native:replacement'))
        self.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
        self.assertEqual(continued['next_action']['payload']['ref'], 'native:replacement')
        (f.flow/'docs/spec.md').write_text('completed recovered design\n')
        f.flow_git('add', 'docs/spec.md'); f.flow_git('commit', '-qm', 'recovered planning')
        candidate = f.flow_git('rev-parse', 'HEAD')
        stopped = f.observation('stopped', 'result', ref='native:replacement')
        stopped['lifecycle']['pages'][0]['response']['data'].append(
            {'id':'native:designer','status':{'type':'idle'}})
        stopped['publication_candidate'] = {'candidate_commit':candidate, 'artifacts':['docs/spec.md'],
            'checks':['design readiness']}
        pending = f.invoke('observe', stopped)['pending']
        f.invoke('decide', {'decision_id':pending['decision_id'], 'subject':pending['subject'],
            'answer':'accept', 'reference':'controller:recovered-plan'})
        published = f.invoke('publication', {'candidate_commit':candidate, 'reference':'controller:recovered-plan'})
        self.assertEqual(published['next_action']['operation'], 'publication-receipt', published)
        received = f.invoke('receive-publication')
        pending = received['pending']
        self.assertIsNotNone(pending, received)
        result = f.invoke('decide', {'decision_id':pending['decision_id'], 'subject':pending['subject'],
            'answer':'accept', 'reference':'controller:accept-recovered-design'})
        self.assertEqual(result['status'], 'accepted', result)
        self.assertEqual(f.state()['accepted']['role_ref'], 'native:replacement')
        self.assertEqual(f.state()['handoff']['authorization']['phase'], phase)
        f.complete_phase()
        topic = entry.topic_read(str(f.root), f.attachment)
        self.assertEqual(topic['current_phase'], 2)
        self.assertEqual(stage_handoff.phase_evidence(saved, role_ref='native:replacement', receiving=True, control_context=f.state()['control']['context'])['attempt']['carrier_ref'],
                         'native:designer')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromName('test_recovered_designer_continues_and_delivers_under_original_phase', AttachedDesignRecoveryTests)
