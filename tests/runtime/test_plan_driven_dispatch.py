"""Plan-bound allocation through the saved Controller, B handoff and Git seam."""
import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_plan_revision_progress as revision_fixture
import workflow_progress as progress
import entry_prepare as entry
import stage_handoff as handoff
import stage_dispatch as dispatch
import workflow_control as control
import execution_plan
import test_stage_transfer as transfer_fixture
import test_workflow_progress as progress_fixture


def inline_plan(requirement_path):
    return {
        "schema_version": 2, "ticket_id": "INLINE-1", "plan_version": 1,
        "spec_ref": {"path": "inline", "section": "brief"},
        "contract_refs": ["standalone:objective"], "blocked_by": [],
        "verifications": [{"id": "V-inline", "behavior": "requested behavior is present",
            "seam": "observed Python output", "commands": ["python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py"]}],
        "steps": [{"id": "inline-step", "target": {"path": "impl.py", "symbol": "run", "operation": "add"},
            "capability": "implement the brief", "approach": ["write the requested implementation"],
            "inputs": ["frozen brief"], "outputs": ["observable result"], "failures": ["invalid input is reported"],
            "depends_on": [], "verification_refs": ["V-inline"],
            "read_only": [requirement_path]}], "artifact_steps": [],
        "behavior_slices": [{"id": "inline-step", "step_ids": ["inline-step"],
            "ac_refs": ["standalone:objective"], "verification_refs": ["V-inline"],
            "read_only": [requirement_path], "shared_state": []}]}


class PlanDrivenDispatchTests(unittest.TestCase):
    def test_controller_allocation_refs_use_flat_source_identity(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        for ref in request["semantic"]["plan_refs"]:
            self.assertEqual(set(ref), {"ticket_id", "step_id", "plan_version",
                "source_commit", "source_sha256", "step_digest"})
            self.assertEqual(ref["source_commit"], flow.state()["scope_authority"]
                             ["execution_plan"]["plan_commit"])

    def existing_inline_request(self, path, source, symbol, approach, operation="modify"):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        if isinstance(source, bytes):
            (case.flow / path).write_bytes(source)
        else:
            (case.flow / path).write_text(source)
        case.flow_git("add", path)
        case.flow_git("commit", "-qm", "existing implementation target")
        request = case.input_for(3)
        scope = request["scope"]
        scope["implementation_paths"] = [path]
        scope["owned_paths"] = [path]
        target = next(item for item in scope["downstream"]["manifest"]
                      if item["owner"] == "implementation")
        target.update(path=path, operation=operation)
        request["authorization"]["scope_digest"] = entry.digest(scope)
        plan = inline_plan(case.requirement_path)
        plan["steps"][0]["target"] = {"path": path, "symbol": symbol,
                                       "operation": operation}
        plan["steps"][0]["approach"] = approach
        request["semantic"]["inline_plan"] = plan
        request.pop("inline_authority", None)
        return case, request

    def test_standalone_existing_python_symbol_must_exist_in_git_baseline(self):
        for operation in ("modify", "delete"):
            with self.subTest(operation=operation):
                case, request = self.existing_inline_request(
                    "impl.py", "def actual():\n    return True\n", "missing",
                    ["change the existing function"], operation)
                with self.assertRaises(entry.PreparationError) as error:
                    handoff.handle(request)
                self.assertEqual(error.exception.code, "execution_plan_incomplete")

    def test_inline_unparseable_python_target_reports_committed_path_at_b_and_c(self):
        for source, cause in ((b"def broken():\n    return '\xff'\n", UnicodeError),
                              ("def broken(:\n    pass\n", SyntaxError)):
            for boundary in ("B", "C"):
                with self.subTest(cause=cause.__name__, boundary=boundary):
                    case, request = self.existing_inline_request(
                        "impl.py", source, "broken", ["change the existing function"])
                    before = case.checkpoint.read_bytes() if case.checkpoint.exists() else None
                    head = case.flow_git("rev-parse", "HEAD")
                    with self.assertRaises(entry.PreparationError) as error:
                        if boundary == "B":
                            handoff.handle(request)
                        else:
                            case.invoke("start", {"handoff": request,
                                "control": {"context": case.context_for(3), "discussion": None}})
                    self.assertEqual(error.exception.code, "execution_plan_incomplete")
                    self.assertIn("impl.py", str(error.exception))
                    self.assertIsInstance(error.exception.__cause__, cause)
                    self.assertEqual(case.checkpoint.read_bytes() if case.checkpoint.exists() else None,
                                     before)
                    self.assertEqual(case.flow_git("rev-parse", "HEAD"), head)

    def test_standalone_python_class_is_not_a_function_target_at_b_and_c(self):
        source = "class Widget:\n    def run(self):\n        return True\n"
        for boundary in ("B", "C"):
            with self.subTest(boundary=boundary):
                case, request = self.existing_inline_request(
                    "impl.py", source, "Widget", ["change the existing function"])
                before = case.checkpoint.read_bytes() if case.checkpoint.exists() else None
                with self.assertRaises(entry.PreparationError) as error:
                    if boundary == "B":
                        handoff.handle(request)
                    else:
                        case.invoke("start", {"handoff": request,
                            "control": {"context": case.context_for(3), "discussion": None}})
                self.assertEqual(error.exception.code, "execution_plan_incomplete")
                self.assertIn("existing Python symbol is missing: Widget", str(error.exception))
                self.assertIn("impl.py", str(error.exception))
                self.assertEqual(case.checkpoint.read_bytes() if case.checkpoint.exists() else None,
                                 before)

    def test_standalone_qualified_python_methods_and_async_function_remain_valid(self):
        source = ("class Widget:\n"
                  "    def instance(self):\n        return True\n"
                  "    @classmethod\n    def klass(cls):\n        return True\n"
                  "    @staticmethod\n    def static():\n        return True\n"
                  "    async def later(self):\n        return True\n"
                  "    class Inner:\n"
                  "        def nested(self):\n            return True\n"
                  "async def top_level():\n    return True\n")
        for symbol in ("Widget.instance", "Widget.klass", "Widget.static",
                       "Widget.later", "Widget.Inner.nested", "top_level"):
            with self.subTest(symbol=symbol):
                case, request = self.existing_inline_request(
                    "impl.py", source, symbol, ["change the existing function"])
                case.context = case.context_for(3)
                started = case.begin("continuous", request)
                self.assertEqual(started["next_action"]["operation"], "invoke-host")

    def test_inline_schema_version_rejects_float_alias(self):
        case, request = self.existing_inline_request(
            "impl.py", "def run():\n    return True\n", "run",
            ["change the existing function"])
        request["semantic"]["inline_plan"]["schema_version"] = 2.0
        with self.assertRaises(entry.PreparationError) as error:
            handoff.handle(request)
        self.assertEqual(error.exception.code, "execution_plan_incomplete")

    def test_standalone_artifact_only_plan_prepares_a_real_agent_ref(self):
        case, request = self.existing_inline_request(
            "prompt.md", "# previous prompt\n", "unused", ["revise the prompt"])
        plan = request["semantic"]["inline_plan"]
        plan["steps"] = []
        plan["artifact_steps"] = [{"id": "prompt-artifact", "path": "prompt.md",
            "operation": "modify", "capability": "revise the prompt",
            "approach": "write the approved prompt text", "verification_refs": ["V-inline"]}]
        revision_fixture.PlanRevisionProgressTests.refresh_fixture_slices(plan)
        saved = handoff.handle(request)
        prepared = dispatch.handle({"protocol": handoff.PROTOCOL,
            "operation": "prepare", "handoff": saved,
            "control": {"context": case.context_for(3), "discussion": None}})
        self.assertEqual(prepared["status"], "prepared")
        authority = prepared["checkpoint"]["context"]["handoff_progress"]["execution_authority"]
        catalog = authority["execution_plan"]["catalog"]
        self.assertEqual(catalog["ordered_step_ids"], ["prompt-artifact"])
        ticket = catalog["tickets"][0]
        ref = execution_plan.step_ref(ticket, plan["artifact_steps"][0])
        group = execution_plan.validate_plan_refs(catalog, [ref], ["prompt.md"], [], [], authority)
        self.assertEqual(group["ordered_step_ids"], ["prompt-artifact"])

    def test_standalone_existing_non_python_sources_need_committed_locations(self):
        examples = (("impl.js", "class Widget { run() { return true; } }\n"
                     "new Widget().run();\n", "Widget.run", "modify"),
                    ("impl.cc", "bool Widget::run() { return true; }\n"
                     "bool call() { return Widget::run(); }\n", "Widget::run", "delete"))
        for path, source, symbol, operation in examples:
            with self.subTest(path=path):
                evidence = [f"source-location: {path}:1",
                            f"production-caller: {path}:2"]
                case, request = self.existing_inline_request(path, source, symbol, evidence, operation)
                saved = handoff.handle(request)
                started = dispatch.handle({"protocol": handoff.PROTOCOL,
                    "operation": "prepare", "handoff": saved,
                    "control": {"context": case.context_for(3), "discussion": None}})
                self.assertEqual(started["status"], "prepared")
                catalog = started["checkpoint"]["context"]["handoff_progress"]["execution_authority"]["execution_plan"]["catalog"]
                self.assertEqual(catalog["tickets"][0]["plan"]["steps"][0]["target"]["symbol"], symbol)

                request["semantic"]["inline_plan"]["steps"][0]["approach"][-1] = \
                    f"production-caller: {path}:99"
                with self.assertRaises(entry.PreparationError) as error:
                    handoff.handle(request)
                self.assertEqual(error.exception.code, "execution_plan_incomplete")

    def test_standalone_controller_checks_existing_source_before_dispatch(self):
        case, request = self.existing_inline_request(
            "impl.py", "def actual():\n    return True\n", "missing",
            ["change the existing function"])
        case.context = case.context_for(3)
        with self.assertRaises(entry.PreparationError) as error:
            case.invoke("start", {"handoff": request,
                "control": {"context": case.context, "discussion": None}})
        self.assertEqual(error.exception.code, "execution_plan_incomplete")
        self.assertNotIn(progress.KEY, progress.read_record(case.checkpoint))

        for path, source, symbol in (
            ("impl.js", "class Widget { run() { return true; } }\n"
             "new Widget().run();\n", "Widget.run"),
            ("impl.cc", "bool Widget::run() { return true; }\n"
             "bool call() { return Widget::run(); }\n", "Widget::run")):
            with self.subTest(path=path):
                evidence = [f"source-location: {path}:1",
                            f"production-caller: {path}:2"]
                case, request = self.existing_inline_request(path, source, symbol, evidence)
                case.context = case.context_for(3)
                started = case.begin("continuous", request)
                self.assertEqual(started["next_action"]["operation"], "invoke-host")
                authority = case.state()["control"]["context"]["handoff_progress"]["execution_authority"]
                self.assertEqual(authority["execution_plan"]["catalog"]["tickets"][0]
                                 ["plan"]["steps"][0]["target"]["symbol"], symbol)

    def active_flow(self, *, generated=False, read_dependency=False):
        return revision_fixture.PlanRevisionProgressTests.new_active_flow(
            self, generated=generated, read_dependency=read_dependency)

    def execution_request(self, flow):
        state = flow.state()
        authority = state["scope_authority"]
        catalog = authority["execution_plan"]["catalog"]
        request = flow.execution_input()
        request["scope"] = copy.deepcopy(state["handoff"]["scope"])
        request["scope"]["baseline"] = flow.flow_git("rev-parse", "HEAD")
        for key in ("downstream", "implementation_paths", "closure_paths"):
            request["scope"][key] = copy.deepcopy(authority["scope"][key])
        request["scope"]["owned_paths"] = ["impl.py"]
        request["authorization"]["scope_digest"] = entry.digest(request["scope"])
        request["readiness"] = progress.consumed_scope_readiness(state, flow.checkpoint)
        request["manifest_baseline"] = authority["scope"]["baseline"]
        ticket = catalog["tickets"][0]
        request["semantic"]["plan_refs"] = [
            execution_plan.step_ref(ticket,
                next(step for step in ticket["plan"]["steps"] if step["id"] == step_id))
            for step_id in catalog["ordered_step_ids"]]
        request["semantic"]["authority_revision"] = authority["revision"]
        request["semantic"]["authority_digest"] = entry.digest(authority)
        request["semantic"]["plan_digest"] = catalog["plan_digest"]
        return request

    def test_current_group_from_activated_authority_prepares_with_generated_map(self):
        flow = self.active_flow(generated=True)
        request = self.execution_request(flow)
        request['scope']['owned_paths'] = ['generated/impl_copy.py', 'impl.py']
        request['authorization']['scope_digest'] = entry.digest(request['scope'])
        response = flow.invoke("allocation", {"allocation_id": "group", "operation": "prepare", "handoff": request})
        self.assertIsNotNone(response.get("next_action"), response)
        self.assertEqual(response["next_action"]["operation"], "invoke-host", response)
        saved = flow.state()["allocations"]["group"]
        record = dispatch.record_for({"record": saved["record"],
            "control": copy.deepcopy(flow.state()["control"])})
        original_input = dispatch.dispatch_input(record)
        payload = handoff.unseal(dispatch.allocation_launch(record))["payload"]
        self.assertEqual(original_input["semantic"]["plan_refs"], request["semantic"]["plan_refs"])
        self.assertEqual(original_input["semantic"]["authority_revision"], 1)
        self.assertEqual(payload["semantic"]["execution_plan"]["plan_digest"],
                         flow.state()["scope_authority"]["execution_plan"]["plan_digest"])
        self.assertIn("generated/impl_copy.py", payload["generated_outputs"]["impl.py"])

    def test_bound_group_release_result_and_accept_use_the_same_plan_identity(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        flow.invoke("allocation", {"allocation_id": "group", "operation": "prepare", "handoff": request})
        slot = flow.state()["allocations"]["group"]
        bound = flow.invoke("allocation", {"allocation_id": "group", "operation": "bind",
            "receipt": flow.receipt(slot["record"], ref="native:plan-agent")})
        release = bound["next_action"]["result"]["write_release"]
        self.assertEqual(release, flow.state()["control"]["context"]["handoff_progress"]["executions"][0]["write_release"])
        data = b"print('verified plan group')\n"
        (flow.flow / "impl.py").write_bytes(data)
        slot = flow.state()["allocations"]["group"]
        payload = {"changed_paths": ["impl.py"], "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()},
            "tests": ["fixture behavior passed"], "write_release": release,
            "plan_refs": request["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        received = flow.invoke("allocation", {"allocation_id": "group", "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref="native:plan-agent"),
            "result": {"delivery_id": "group-result", "status": "completed", "payload": payload}})
        self.assertEqual(received["next_action"]["result"]["status"], "received", received)
        accepted = flow.invoke("allocation", {"allocation_id": "group", "operation": "accept",
            "decision": {"reference": "dispatcher:accepted-current-group"}})
        self.assertEqual(accepted["next_action"]["result"]["status"], "accepted", accepted)
        authority = flow.state()['scope_authority']
        execution = flow.state()['control']['context']['handoff_progress']['executions'][0]
        self.assertEqual(execution_plan.current_accepted_steps(
            authority['execution_plan']['catalog'], [execution], authority),
            set(authority['execution_plan']['catalog']['ordered_step_ids']))

    def test_multifile_group_cannot_release_with_only_one_target_owned(self):
        flow = self.active_flow(read_dependency=True)
        request = self.execution_request(flow)
        refs = [ref for ref in request['semantic']['plan_refs']
                if ref['step_id'] in {'FP1-01', 'FP1-03'}]
        self.assertEqual([ref['step_id'] for ref in refs], ['FP1-01', 'FP1-03'])
        request['semantic']['plan_refs'] = refs
        with self.assertRaises(entry.PreparationError) as error:
            handoff.prepare(request)
        self.assertEqual(error.exception.code, 'execution_plan_stale')

        prepared = flow.invoke('allocation', {'allocation_id': 'incomplete-group',
                                             'operation': 'prepare', 'handoff': request})
        self.assertEqual(prepared['status'], 'blocked', prepared)
        self.assertEqual(prepared['error']['code'], 'execution_plan_stale')
        self.assertIsNone(prepared['next_action'])

        context = flow.state()['control']['context']
        authority = context['handoff_progress']['plan_authority']
        evidence = {'task_id': 'incomplete-group', 'paths': ['impl.py'],
                    'read_only': request['scope']['protected_paths'],
                    'behavior': 'approved behavior',
                    'tests': ['python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py'],
                    'git_operations': [], 'configuration': request['configuration'],
                    'plan_refs': refs, 'plan_authority': authority,
                    'group_digest': entry.digest([authority, refs])}
        with self.assertRaises(entry.PreparationError) as error:
            control.transition({'schema_version': context['schema_version'],
                'actor_ref': context['controller_ref'], 'context': context,
                'action': 'plan-execution', 'evidence': evidence})
        self.assertEqual(error.exception.code, 'execution_plan_stale')

    def test_old_accepted_owner_cannot_complete_an_unowned_step_ref(self):
        flow = self.active_flow(read_dependency=True)
        authority = flow.state()['scope_authority']
        catalog = authority['execution_plan']['catalog']
        ticket = catalog['tickets'][0]
        revision_fixture.PlanRevisionProgressTests.accept_planned_step(
            self, flow, ticket, 'FP1-01', 'impl.py', b'def run():\n    return 1\n',
            'single-owner')
        accepted = copy.deepcopy(flow.state()['control']['context']['handoff_progress']['executions'][0])
        owned_completion = execution_plan.current_accepted_steps(catalog, [accepted], authority)
        self.assertIn('FP1-01', owned_completion)
        missing_step = next(step for step in ticket['plan']['steps'] if step['id'] == 'FP1-03')
        accepted['plan_refs'].append(execution_plan.step_ref(ticket, missing_step))
        completed = execution_plan.current_accepted_steps(catalog, [accepted], authority)
        self.assertNotIn('FP1-03', completed)

    def test_complete_multifile_group_owns_both_final_fingerprints(self):
        flow = self.active_flow(read_dependency=True)
        request = self.execution_request(flow)
        request['semantic']['plan_refs'] = [ref for ref in request['semantic']['plan_refs']
                                            if ref['step_id'] in {'FP1-01', 'FP1-03'}]
        request['scope']['owned_paths'] = ['impl.py', 'independent.py']
        request['authorization']['scope_digest'] = entry.digest(request['scope'])
        prepared = flow.invoke('allocation', {'allocation_id': 'complete-group',
                                             'operation': 'prepare', 'handoff': request})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = flow.state()['allocations']['complete-group']
        bound = flow.invoke('allocation', {'allocation_id': 'complete-group', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:complete-group')})
        release = bound['next_action']['result']['write_release']
        contents = {'impl.py': b'def run():\n    return 1\n',
                    'independent.py': b'def independent():\n    return 2\n'}
        for relative, body in contents.items():
            (flow.flow / relative).write_bytes(body)
        slot = flow.state()['allocations']['complete-group']
        result = {'changed_paths': sorted(contents),
                  'file_hashes': {relative: hashlib.sha256(body).hexdigest()
                                  for relative, body in contents.items()},
                  'tests': ['two public behaviors passed'], 'write_release': release,
                  'plan_refs': request['semantic']['plan_refs'],
                  'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}
        flow.invoke('allocation', {'allocation_id': 'complete-group', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:complete-group'),
            'result': {'delivery_id': 'complete-group-result', 'status': 'completed',
                       'payload': result}})
        accepted = flow.invoke('allocation', {'allocation_id': 'complete-group', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:complete-group'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted')
        authority = flow.state()['scope_authority']
        execution = flow.state()['control']['context']['handoff_progress']['executions'][0]
        actual = {relative: progress.control_git.file_fingerprint(flow.flow, relative)
                  for relative in contents}
        self.assertEqual(execution_plan.accepted_owned_fingerprints(execution), actual)
        self.assertEqual(execution_plan.current_accepted_steps(
            authority['execution_plan']['catalog'], [execution], authority, actual),
            {'FP1-01', 'FP1-03'})

    def test_unowned_accepted_ref_cannot_unlock_a_dependent_group(self):
        original_slices = revision_fixture.PlanRevisionProgressTests.refresh_fixture_slices
        def separate_prerequisite_owners(plan):
            # Each accepted owner is a complete slice; downstream still needs both.
            steps = {step['id']: step for step in plan['steps']}
            steps['FP1-02']['read_only'] = []
            steps['FP1-03']['read_only'] = ['impl.py']
            steps['FP1-04']['depends_on'] = ['FP1-02', 'FP1-03']
            original_slices(plan)
        with patch.object(revision_fixture.PlanRevisionProgressTests, 'refresh_fixture_slices',
                          side_effect=separate_prerequisite_owners):
            flow = revision_fixture.PlanRevisionProgressTests.new_active_flow(
                self, read_dependency=True)
        authority = flow.state()['scope_authority']
        catalog = authority['execution_plan']['catalog']
        ticket = catalog['tickets'][0]
        for identity, relative, body in (
                ('FP1-01', 'impl.py', b'def run():\n    return 1\n'),
                ('FP1-02', 'later.py', b'def later():\n    return 2\n')):
            revision_fixture.PlanRevisionProgressTests.accept_planned_step(
                self, flow, ticket, identity, relative, body, 'owner-' + identity)
        results = copy.deepcopy(flow.state()['control']['context']['handoff_progress']['executions'])
        independent = next(step for step in ticket['plan']['steps'] if step['id'] == 'FP1-03')
        results[-1]['plan_refs'].append(execution_plan.step_ref(ticket, independent))
        downstream = next(step for step in ticket['plan']['steps'] if step['id'] == 'FP1-04')
        with self.assertRaises(entry.PreparationError) as error:
            execution_plan.validate_plan_refs(catalog,
                [execution_plan.step_ref(ticket, downstream)], ['downstream.py'], [],
                results, authority)
        self.assertEqual(error.exception.code, 'execution_plan_stale')

    def test_fake_accepted_ref_cannot_make_git_candidate_ready(self):
        flow = progress_fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        request = flow.input_for(3)
        scope = request['scope']
        scope['implementation_paths'] = scope['owned_paths'] = ['helper.py', 'impl.py']
        helper_manifest = copy.deepcopy(next(item for item in scope['downstream']['manifest']
                                             if item['path'] == 'impl.py'))
        helper_manifest['path'] = 'helper.py'
        scope['downstream']['manifest'].append(helper_manifest)
        scope['downstream']['manifest'].sort(key=lambda item: item['path'])
        request['authorization']['scope_digest'] = entry.digest(scope)
        plan = request['semantic']['inline_plan']
        helper_step = copy.deepcopy(plan['steps'][0])
        helper_step['id'] = 'inline-helper'
        helper_step['target'] = {'path': 'helper.py', 'symbol': 'helper', 'operation': 'add'}
        helper_step['read_only'] = []
        plan['steps'].append(helper_step)
        revision_fixture.PlanRevisionProgressTests.refresh_fixture_slices(plan)
        flow.context = flow.context_for(3)
        flow.begin('continuous', request)
        flow.invoke('observe', flow.observation(ref='native:dispatcher'))
        (flow.flow / 'impl.py').write_bytes(b'def run():\n    return 1\n')
        (flow.flow / 'helper.py').write_bytes(b'def helper():\n    return 2\n')
        flow.flow_git('add', 'impl.py', 'helper.py')
        flow.flow_git('commit', '-qm', 'original complete implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.accept_fixture_candidate(candidate)
        flow.complete_fixture_local_reviews(candidate, '-fake-accepted-negative')
        owner = progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint))
        coverage = owner.build_review_coverage(candidate, delivery=False)
        before = flow.checkpoint.read_bytes()
        context = copy.deepcopy(owner.state['control']['context'])
        # Only the caller's detached negative input claims a partial result.
        # The original C, allocation acceptance and Ticket coverage stay real.
        context['handoff_progress'] = dict(context['handoff_progress'])
        context['handoff_progress']['executions'] = []
        context['handoff_progress']['archived_executions'] = []
        flow.fixture_accepted_execution(context, candidate)
        execution = context['handoff_progress']['executions'][0]
        execution['paths'] = ['impl.py']
        execution['file_hashes'] = {'impl.py': execution['file_hashes']['impl.py']}
        execution['git_result_snapshot'] = {'impl.py': execution['git_result_snapshot']['impl.py']}
        execution['plan_refs'] = [ref for ref in execution['plan_refs'] if ref['step_id'] != helper_step['id']]
        authority = context['handoff_progress']['execution_authority']
        ticket = authority['execution_plan']['catalog']['tickets'][0]
        execution['plan_refs'].append(execution_plan.step_ref(ticket, helper_step))
        execution['group_digest'] = entry.digest([execution['plan_authority'], execution['plan_refs']])
        checkpoint = context['handoff_progress']
        checks = transfer_fixture.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        evidence = {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': context['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks, 'ticket_coverage': coverage,
            'source': {'adapter': 'fixture', 'call_ref': 'focused-incomplete',
                'response_ref': 'focused-incomplete-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}}
        with self.assertRaises(control.ControlError) as error:
            progress.control_git.verified_transition({'repository': str(flow.flow),
                'baseline': request['scope']['baseline'], 'request': {
                    'schema_version': context['schema_version'], 'actor_ref': 'task',
                    'context': context, 'action': 'candidate-ready', 'evidence': evidence}})
        self.assertIn('current function step', str(error.exception))
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_later_native_allocation_reads_accepted_implementation_file(self):
        flow = self.active_flow(read_dependency=True)
        first = self.execution_request(flow)
        first['semantic']['plan_refs'] = first['semantic']['plan_refs'][:1]
        flow.invoke('allocation', {'allocation_id': 'first', 'operation': 'prepare', 'handoff': first})
        slot = flow.state()['allocations']['first']
        bound = flow.invoke('allocation', {'allocation_id': 'first', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:first')})
        release = bound['next_action']['result']['write_release']
        data = b'def prepare():\n    return "accepted"\n'
        (flow.flow / 'impl.py').write_bytes(data)
        slot = flow.state()['allocations']['first']
        payload = {'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
            'tests': ['fixture behavior passed'], 'write_release': release,
            'plan_refs': first['semantic']['plan_refs'],
            'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}
        flow.invoke('allocation', {'allocation_id': 'first', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:first'),
            'result': {'delivery_id': 'first-result', 'status': 'completed', 'payload': payload}})
        accepted = flow.invoke('allocation', {'allocation_id': 'first', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accepted-first'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted')
        accepted_impl = flow.state()['control']['context']['handoff_progress']['executions'][0][
            'git_result_snapshot']['impl.py']

        second = self.execution_request(flow)
        second['scope']['owned_paths'] = ['later.py']
        second['scope']['protected_paths'] = sorted([*second['scope']['protected_paths'], 'impl.py'])
        second['authorization']['scope_digest'] = entry.digest(second['scope'])
        second['semantic']['plan_refs'] = [ref for ref in second['semantic']['plan_refs']
                                           if ref['step_id'] == 'FP1-02']
        self.assertEqual(len(second['semantic']['plan_refs']), 1)
        prepared = flow.invoke('allocation', {'allocation_id': 'later', 'operation': 'prepare',
            'handoff': second})
        self.assertIsNotNone(prepared['next_action'], prepared)
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = flow.state()['allocations']['later']
        bound = flow.invoke('allocation', {'allocation_id': 'later', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:later')})
        execution = flow.state()['control']['context']['handoff_progress']['executions'][-1]
        self.assertEqual(execution['agent_ref'], 'native:later')
        self.assertIn('impl.py', execution['read_only'])
        self.assertEqual(execution['git_snapshot']['files']['impl.py'], accepted_impl)
        self.assertEqual(bound['next_action']['result']['write_release'], execution['write_release'])

    def test_control_rejects_forged_step_even_with_matching_group_digest(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        context = flow.state()["control"]["context"]
        authority = context["handoff_progress"]["plan_authority"]
        forged = copy.deepcopy(request["semantic"]["plan_refs"])
        forged[0]["step_id"] = "unknown-step"
        evidence = {"task_id": "forged-group", "paths": ["impl.py"],
            "read_only": request["scope"]["protected_paths"], "behavior": "approved behavior",
            "tests": ["python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py"],
            "git_operations": [], "configuration": request["configuration"],
            "plan_refs": forged, "plan_authority": authority,
            "group_digest": entry.digest([authority, forged])}
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            control.transition({"schema_version": context["schema_version"],
                "actor_ref": context["controller_ref"], "context": context,
                "action": "plan-execution", "evidence": evidence})
        self.assertEqual(error.exception.code, "execution_plan_stale")
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_control_rejects_non_scalar_plan_ref_before_hash_lookup(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        context = flow.state()["control"]["context"]
        authority = context["handoff_progress"]["plan_authority"]
        before = flow.checkpoint.read_bytes()
        for field, value in (("step_id", ["FP1-01"]),
                             ("step_id", {"id": "FP1-01"}),
                             ("plan_version", True),
                             ("source_commit", ["unexpected"])):
            with self.subTest(field=field, value=value):
                refs = copy.deepcopy(request["semantic"]["plan_refs"])
                refs[0][field] = value
                evidence = {"task_id": "malformed-ref", "paths": ["impl.py"],
                    "read_only": request["scope"]["protected_paths"], "behavior": "approved behavior",
                    "tests": ["python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py"],
                    "git_operations": [], "configuration": request["configuration"],
                    "plan_refs": refs, "plan_authority": authority,
                    "group_digest": entry.digest([authority, refs])}
                with self.assertRaises(entry.PreparationError) as error:
                    control.transition({"schema_version": context["schema_version"],
                        "actor_ref": context["controller_ref"], "context": context,
                        "action": "plan-execution", "evidence": evidence})
                self.assertEqual(error.exception.code, "execution_plan_stale")
                self.assertIn(f"plan_refs[0].{field}", str(error.exception))
                self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_dependency_requires_stopped_exact_current_result(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        authority = flow.state()['scope_authority']
        refs = request['semantic']['plan_refs']
        current = flow.state()['control']['context']['handoff_progress']['plan_authority']
        first = {'state': 'accepted', 'stopped': False, 'plan_authority': current,
                 'plan_refs': [refs[0]], 'paths': ['impl.py'],
                 'git_snapshot': {'files': {'impl.py': None}},
                 'git_result_snapshot': {'impl.py': 'f' * 64}}
        with self.assertRaises(entry.PreparationError) as error:
            execution_plan.validate_plan_refs(authority['execution_plan']['catalog'], [refs[1]],
                ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(error.exception.code, 'execution_plan_stale')
        first['stopped'] = True
        first['plan_refs'][0] = {**refs[0], 'step_digest': 'historical-step'}
        with self.assertRaises(entry.PreparationError) as error:
            execution_plan.validate_plan_refs(authority['execution_plan']['catalog'], [refs[1]],
                ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(error.exception.code, 'execution_plan_stale')
        first['plan_refs'][0] = refs[0]
        group = execution_plan.validate_plan_refs(authority['execution_plan']['catalog'], [refs[1]],
            ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(group['ordered_step_ids'], ['FP1-02'])
        first['plan_authority'] = {**current, 'revision': current['revision'] - 1}
        with self.assertRaises(entry.PreparationError):
            execution_plan.validate_plan_refs(authority['execution_plan']['catalog'], [refs[1]],
                ['impl.py'], request['scope']['protected_paths'], [first], authority)

    def test_activated_revision_rejects_old_refs_and_releases_new_group(self):
        flow = self.active_flow(generated=True)
        controller_request, controller_settings = copy.deepcopy(flow.request), copy.deepcopy(flow.settings)
        old_request = self.execution_request(flow)
        report = revision_fixture.PlanRevisionProgressTests.report(self, flow)
        revised_validation = copy.deepcopy(flow.state()['control']['context']['handoff_progress']['validation_plan'])
        revised_validation['review_required'][0]['command'] += ' revised'
        report['validation_plan'] = revised_validation
        old_manifest = flow.state()["scope_authority"]["scope"]["downstream"]["manifest"]
        additional = copy.deepcopy(next(item for item in old_manifest
                                        if item["path"] == "generated/impl_copy.py"))
        additional["path"] = "generated/second.py"
        report["manifest"] = sorted([*old_manifest, additional], key=lambda item: item["path"])
        report["generated_outputs"] = {"impl.py": ["generated/impl_copy.py", "generated/second.py"]}
        report["report"]["manifest_diff"] = progress.stage_scope.manifest_diff(old_manifest, report["manifest"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        release = prepared["next_action"]["write_release"]
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("Keep the current behavior with a revised private step")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "private plan revision", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": release,
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        self.assertEqual(flow.state()["scope_authority"]["revision"], 2)
        envelope = flow.state()["scope_authority"]["execution_plan"]
        self.assertEqual(envelope["original_scope_revision"], 1)
        self.assertEqual(envelope["plan_commit"], flow.flow_git("rev-parse", "HEAD"))
        self.assertEqual({source["commit"] for source in envelope["sources"]}, {envelope["plan_commit"]})
        for ticket in envelope["catalog"]["tickets"]:
            self.assertEqual(ticket["source"]["plan_version"], ticket["plan_version"])
            self.assertEqual(ticket["source"]["commit"], envelope["plan_commit"])
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['validation_plan'],
                         revised_validation)
        dispatcher_port = {'record': flow.state()['dispatch'],
                           'control': copy.deepcopy(flow.state()['control'])}
        dispatch.record_for(dispatcher_port)
        self.assertEqual(dispatcher_port['_current_stage3_progress']['plan_authority']['revision'], 2)
        forged_port = copy.deepcopy(dispatcher_port)
        forged_port['control']['context']['handoff_progress']['validation_plan']['review_required'][0]['command'] = 'forged-check'
        forged_port['control']['context']['handoff_progress']['plan_digest'] = entry.digest(
            forged_port['control']['context']['handoff_progress']['validation_plan'])
        with self.assertRaises(control.ControlError):
            dispatch.record_for(forged_port)
        wrong_control = copy.deepcopy(dispatcher_port)
        wrong_control['control']['context']['preference']['stage_current'] = not \
            wrong_control['control']['context']['preference']['stage_current']
        with self.assertRaises(entry.PreparationError):
            dispatch.record_for(wrong_control)
        wrong_checkpoint = flow.checkpoint.with_name('other-controller.json')
        wrong_checkpoint.write_bytes(flow.checkpoint.read_bytes())
        wrong_record = handoff.unseal(dispatcher_port['record'])
        wrong_record['scope_checkpoint_path'] = str(wrong_checkpoint)
        with self.assertRaises(entry.PreparationError):
            dispatch.record_for({'record': handoff.seal(wrong_record),
                                 'control': copy.deepcopy(dispatcher_port['control'])})
        request = self.execution_request(flow)
        request['scope']['owned_paths'] = ['generated/impl_copy.py', 'generated/second.py', 'impl.py']
        request['authorization']['scope_digest'] = entry.digest(request['scope'])
        saved = handoff.handle(request)
        dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved,
                         "control": {"context": flow.state()["control"]["context"], "discussion": None}})
        current = flow.invoke("allocation", {"allocation_id": "current-group", "operation": "prepare",
            "handoff": request})
        self.assertIsNotNone(current.get("next_action"), current)
        self.assertEqual(current["next_action"]["operation"], "invoke-host", current)
        slot = flow.state()["allocations"]["current-group"]
        record = dispatch.record_for({"record": slot["record"], "control": flow.state()["control"]})
        payload = handoff.unseal(dispatch.allocation_launch(record))["payload"]
        self.assertEqual(payload["current_scope_authority"]["authority_revision"], 2)
        self.assertEqual(payload["current_scope_authority"]["authority_digest"],
                         entry.digest(flow.state()["scope_authority"]))
        self.assertEqual(payload["generated_outputs"]["impl.py"],
                         ["generated/impl_copy.py", "generated/second.py"])
        self.assertIn("generated/second.py", payload["scope"]["implementation_paths"])
        slot = flow.state()["allocations"]["current-group"]
        bound = flow.invoke("allocation", {"allocation_id": "current-group", "operation": "bind",
            "receipt": flow.receipt(slot["record"], ref="native:revised-agent")})
        self.assertEqual(bound["next_action"]["result"]["status"], "bound", bound)
        self.assertEqual(bound["next_action"]["result"]["write_release"],
                         flow.state()["control"]["context"]["handoff_progress"]["executions"][0]["write_release"])
        stale_request = copy.deepcopy(request)
        stale_request["semantic"]["plan_refs"] = old_request["semantic"]["plan_refs"]
        with self.assertRaises(entry.PreparationError) as stale:
            handoff.handle(stale_request)
        self.assertEqual(stale.exception.code, 'execution_plan_stale')
        implementation = b"print('revised staged behavior')\n"
        (flow.flow / 'impl.py').write_bytes(implementation)
        for relative in ('generated/impl_copy.py', 'generated/second.py'):
            generated = flow.flow / relative
            generated.parent.mkdir(parents=True, exist_ok=True)
            generated.write_bytes(implementation)
        slot = flow.state()['allocations']['current-group']
        owned_paths = ['generated/impl_copy.py', 'generated/second.py', 'impl.py']
        result_payload = {'changed_paths': owned_paths,
            'file_hashes': {relative: hashlib.sha256(implementation).hexdigest()
                            for relative in owned_paths},
            'tests': ['revised behavior passed'],
            'write_release': bound['next_action']['result']['write_release'],
            'plan_refs': request['semantic']['plan_refs'],
            'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}
        received = flow.invoke('allocation', {'allocation_id': 'current-group', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:revised-agent'),
            'result': {'delivery_id': 'revised-group-result', 'status': 'completed',
                       'payload': result_payload}})
        self.assertEqual(received['next_action']['result']['status'], 'received', received)
        accepted_group = flow.invoke('allocation', {'allocation_id': 'current-group', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:revised-group'}})
        self.assertEqual(accepted_group['next_action']['result']['status'], 'accepted', accepted_group)
        flow.request, flow.settings = controller_request, controller_settings
        flow.flow_git('add', *owned_paths)
        flow.flow_git('commit', '-qm', 'revised staged implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')

        payload = {'artifacts': ['impl.py'], 'checks': ['revised behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': []}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        accepted_payload = flow.state()['accepted']['payload']
        self.assertEqual(accepted_payload['verification']['validation_plan'], revised_validation)
        self.assertEqual(flow.state()['handoff']['readiness']['scope_revision'], 1)
        current_progress = flow.state()['control']['context']['handoff_progress']
        bad_authority = copy.deepcopy(accepted_payload)
        bad_authority['verification']['plan_authority']['revision'] = 1
        with self.assertRaises((entry.PreparationError, control.ControlError)):
            handoff.verify_result(3, bad_authority, flow.binding,
                flow.state()['handoff']['scope'], str(flow.flow), 'native:dispatcher',
                current_progress=current_progress)
        bad_n5 = copy.deepcopy(accepted_payload)
        bad_n5['verification']['plan_digest'] = '0' * 64
        with self.assertRaises((entry.PreparationError, control.ControlError)):
            handoff.verify_result(3, bad_n5, flow.binding,
                flow.state()['handoff']['scope'], str(flow.flow), 'native:dispatcher',
                current_progress=current_progress)
        spec.write_text(spec.read_text() + '\nunauthorized planning bytes\n')
        flow.flow_git('add', 'docs/spec.md')
        flow.flow_git('commit', '-qm', 'unactivated planning change')
        with self.assertRaisesRegex((entry.PreparationError, control.ControlError),
                                    'planning bytes changed|unapproved commit changed'):
            handoff.verify_result(3, accepted_payload, flow.binding,
                flow.state()['handoff']['scope'], str(flow.flow), 'native:dispatcher',
                current_progress=current_progress)

    def test_new_stage_three_rejects_explicit_direct_policy(self):
        case = transfer_fixture.StageTransferTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["implementation_policy"] = {"mode": "direct"}
        with self.assertRaises(entry.PreparationError) as error:
            handoff.handle(request)
        self.assertEqual(error.exception.code, "invalid_policy")

    def test_standalone_inline_function_plan_needs_no_ticket_document(self):
        case = transfer_fixture.StageTransferTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority", None)
        saved = handoff.handle(request)
        payload = handoff.render(saved)["payload"]
        authority = handoff.unseal(saved)["inline_authority"]
        catalog = execution_plan.current_catalog(authority)
        self.assertEqual(catalog["tickets"][0]["plan"]["ticket_id"], "INLINE-1")
        self.assertEqual(authority["kind"], "inline")
        self.assertEqual(payload["execution_plan"]["plan_digest"], catalog["plan_digest"])

    def test_standalone_inline_group_allocates_under_original_controller(self):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority", None)
        case.context = case.context_for(3)
        case.begin("continuous", request)
        case.invoke("observe", case.observation(ref="native:dispatcher"))
        authority = case.state()["handoff"]["inline_authority"]
        execution = case.execution_input()
        execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = entry.digest(execution["scope"])
        record = authority["execution_plan"]["catalog"]["tickets"][0]
        step = record["plan"]["steps"][0]
        execution["semantic"].update(inline_plan=request["semantic"]["inline_plan"],
            plan_refs=[execution_plan.step_ref(record, step)],
            authority_revision=authority["revision"], authority_digest=entry.digest(authority),
            plan_digest=authority["execution_plan"]["plan_digest"])
        prepared = case.invoke("allocation", {"allocation_id": "inline-group", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)

    def test_inline_final_integration_recovery_keeps_protected_source_locked(self):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority", None)
        case.context = case.context_for(3)
        case.begin("continuous", request)
        case.invoke("observe", case.observation(ref="native:dispatcher"))
        with patch.dict(case.settings), patch.dict(case.request["host"]):
            execution = case.execution_input()
            allocated = case.invoke("allocation", {"allocation_id": "original-inline",
                "operation": "prepare", "handoff": execution})
            self.assertEqual(allocated["next_action"]["operation"], "invoke-host", allocated)
            slot = case.state()["allocations"]["original-inline"]
            bound = case.invoke("allocation", {"allocation_id": "original-inline",
                "operation": "bind", "receipt": case.receipt(slot["record"], ref="native:inline-agent")})
            content = b"def run():\n    return 'original'\n"
            (case.flow / "impl.py").write_bytes(content)
            case.invoke("allocation", {"allocation_id": "original-inline", "operation": "receive",
                "receipt": case.receipt(slot["record"], "stopped", "result", ref="native:inline-agent"),
                "result": {"delivery_id": "original-inline", "status": "completed", "payload": {
                    "changed_paths": ["impl.py"], "file_hashes": {"impl.py": hashlib.sha256(content).hexdigest()},
                    "tests": ["inline behavior passed"],
                    "write_release": bound["next_action"]["result"]["write_release"],
                    "plan_refs": execution["semantic"]["plan_refs"],
                    "plan_authority": case.state()["control"]["context"]["handoff_progress"]["plan_authority"]}}})
            accepted = case.invoke("allocation", {"allocation_id": "original-inline",
                "operation": "accept", "decision": {"reference": "dispatcher:inline"}})
            self.assertEqual(accepted["next_action"]["result"]["status"], "accepted")
        case.flow_git("add", "impl.py")
        case.flow_git("commit", "-qm", "accepted inline implementation")
        candidate = case.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["inline behavior passed"],
            "candidate_commit": candidate,
            "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"}
                       for axis in ("standards", "spec")},
            "verification": {"candidate": candidate}}
        original = case.accept_current(payload, "native:dispatcher", fixture_execution=False)
        self.assertEqual(original["status"], "accepted", original)
        predecessor = case.state()["accepted"]
        (case.root / "other-task.md").write_text("parallel target work\n")
        case.git("add", "other-task.md")
        case.git("commit", "-qm", "parallel target work")
        case.context = case.context_for(4)
        case.begin("continuous", case.input_for(4, predecessor))
        case.invoke("observe", case.observation(ref="native:closure"))
        observed = case.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": candidate, "merge": None,
            "ancestor_verified": False, "changed_paths": [], "binding": case.binding, "checks": [],
            "worktree_removed": False, "branch_removed": False, "disposition": "deleted",
            "disposition_verified": False, "implementation_problem": "target advanced"}
        self.assertEqual(case.invoke("observe", observed)["next_action"]["operation"],
                         "implementation-recovery")
        recovered = case.invoke("implementation-recovery", {"reference": "controller:inline-race",
            "closure_digest": case.state()["handoff"]["digest"]})
        self.assertEqual(case.state()["stage"], 3, recovered)
        case.invoke("observe", case.observation("idle", "result", ref="native:dispatcher"))
        case.flow_git("merge", "--no-edit", "main")
        integrated = case.flow_git("rev-parse", "HEAD")
        case.invoke("observe", case.observation("stopped", "result", ref="native:dispatcher"))
        inline_authority = case.state()["control"]["context"]["handoff_progress"]["execution_authority"]
        inline_catalog = inline_authority["execution_plan"]["catalog"]
        self.assertEqual(inline_authority["kind"], "inline")
        self.assertIsNone(inline_catalog["commit"])
        self.assertNotIn("sources", inline_catalog)
        self.assertEqual(len(inline_catalog["tickets"]), 1)
        requirement = case.flow / case.requirement_path
        original_requirement = requirement.read_bytes()
        before_rejection = copy.deepcopy(case.state())
        requirement.write_bytes(original_requirement + b"unauthorized inline source change\n")
        try:
            rejected = case.invoke("control", {"action": "capture-integration",
                "evidence": {"reference": "controller:inline-protected-negative"},
                "receipt": {"controller_ref": "task", "reference": "controller:inline-protected-negative"}})
            self.assertEqual(rejected["status"], "blocked", rejected)
        finally:
            requirement.write_bytes(original_requirement)
            restored = progress.Progress(case.checkpoint, progress.read_active_owner(case.checkpoint))
            for field in ("status", "error", "blocked_from"):
                if field in before_rejection:
                    restored.state[field] = copy.deepcopy(before_rejection[field])
                else:
                    restored.state.pop(field, None)
            restored.save()
        captured = case.invoke("control", {"action": "capture-integration",
            "evidence": {"reference": "controller:inline-race"},
            "receipt": {"controller_ref": "task", "reference": "controller:inline-race"}})
        self.assertNotEqual(captured["status"], "blocked", captured)
        current = case.state()["control"]["context"]["handoff_progress"]
        self.assertEqual(current["integration_retained"]["paths"], [])
        self.assertIsNotNone(current["integration_source"])
        with patch.dict(case.settings, thread_id="dispatcher-runtime"), patch.dict(case.request["host"]):
            direct = case.inherited_execution_input()
            direct["scope"]["owned_paths"] = copy.deepcopy(current["integration_source"]["pending_paths"])
            direct["manifest_baseline"] = case.state()["scope_authority"]["scope"]["baseline"]
            direct["authorization"]["scope_digest"] = entry.digest(direct["scope"])
            direct["semantic"].update(adopt_paths=copy.deepcopy(direct["scope"]["owned_paths"]),
                adoption_snapshot_digest=current["integration_source"]["snapshot_digest"])
            with self.assertRaises(entry.PreparationError) as error:
                handoff.prepare(direct)
            self.assertEqual(error.exception.code, 'authority_missing', str(error.exception))
            pinned = handoff.prepare(direct, checkpoint_path=str(case.checkpoint.resolve()))
            with self.assertRaises(entry.PreparationError) as error:
                dispatch.handle({'protocol': handoff.PROTOCOL, 'operation': 'prepare',
                    'handoff': pinned, 'control': copy.deepcopy(case.state()['control']),
                    'scope_checkpoint_path': str(case.checkpoint.resolve())})
            self.assertEqual(error.exception.code, 'authority_missing')
            forged = case.checkpoint.with_name('forged-inline-recovery.json')
            forged.write_bytes(case.checkpoint.read_bytes())
            import shutil
            shutil.copytree(progress.state_store.open_store(case.checkpoint).directory,
                            progress.state_store.open_store(forged).directory)
            with self.assertRaises(entry.PreparationError) as error:
                handoff.prepare(direct, checkpoint_path=str(forged.resolve()))
            self.assertEqual(error.exception.code, 'invalid_checkpoint')
            self.assertIn('scope identity differs from current root', str(error.exception))
            original_projection = progress.read_projection
            checkpoint_bytes = case.checkpoint.read_bytes()
            for field in ('attempt', 'binding', 'authority', 'snapshot'):
                def stale_scope(path, *, projection, **kwargs):
                    value = original_projection(path, projection=projection, **kwargs)
                    if Path(path) == case.checkpoint and projection == 'scope':
                        value = copy.deepcopy(value)
                        state = value.record[progress.KEY]
                        if field == 'attempt':
                            state['integration_recovery']['carrier']['attempt'] = '0' * 64
                        elif field == 'binding':
                            state['handoff']['binding']['branch'] = 'codex/other-flow'
                        elif field == 'authority':
                            state['scope_authority']['revision'] += 1
                        else:
                            state['integration_recovery']['source_digest'] = '0' * 64
                    return value
                with patch.object(progress, 'read_projection', side_effect=stale_scope):
                    with self.subTest(stale_pin=field), self.assertRaises((entry.PreparationError, control.ControlError)):
                        handoff.prepare(direct, checkpoint_path=str(case.checkpoint.resolve()))
                self.assertEqual(case.checkpoint.read_bytes(), checkpoint_bytes)
        with patch.dict(case.settings, thread_id="dispatcher-runtime"), patch.dict(case.request["host"]):
            execution = case.inherited_execution_input()
            execution["scope"]["owned_paths"] = copy.deepcopy(current["integration_source"]["pending_paths"])
            execution["authorization"]["scope_digest"] = entry.digest(execution["scope"])
            execution["semantic"].update(adopt_paths=copy.deepcopy(execution["scope"]["owned_paths"]),
                adoption_snapshot_digest=current["integration_source"]["snapshot_digest"])
            self.assertIsNone(execution.get("readiness"))
            requirement = case.flow / case.requirement_path
            original_requirement = requirement.read_bytes()
            before_rejection = copy.deepcopy(case.state())
            requirement.write_bytes(original_requirement + b"unauthorized inline source change\n")
            try:
                rejected = case.invoke("allocation", {"allocation_id": "tampered-inline",
                    "operation": "prepare", "handoff": execution})
                self.assertEqual(rejected["status"], "blocked", rejected)
                self.assertNotIn("write_release", rejected.get("next_action") or {})
            finally:
                requirement.write_bytes(original_requirement)
                restored = progress.Progress(case.checkpoint, progress.read_active_owner(case.checkpoint))
                for field in ("status", "error", "blocked_from"):
                    if field in before_rejection:
                        restored.state[field] = copy.deepcopy(before_rejection[field])
                    else:
                        restored.state.pop(field, None)
                restored.save()
        original_record_for = dispatch.record_for
        def original_checkpoint_reader(request):
            if request.get('operation') is None and request.get('record') is not None:
                request = {**request, 'scope_checkpoint_path': str(case.checkpoint.resolve())}
            return original_record_for(request)
        with patch.object(dispatch, 'record_for', side_effect=original_checkpoint_reader):
            case.revalidate_integrated_execution()
        payload["candidate_commit"] = integrated
        payload["review"] = {axis: {"candidate": integrated,
            "reviewer_ref": "integrated-" + axis, "status": "accepted"}
            for axis in ("standards", "spec")}
        replaced = case.accept_current(payload, "native:dispatcher", fixture_execution=False,
                                       evidence_suffix="inline-integrated")
        self.assertEqual(replaced["status"], "accepted", replaced)
        self.assertNotEqual(case.state()["accepted"]["digest"], predecessor["digest"])

    def test_inline_revision_switches_current_plan_without_document_commit(self):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority", None)
        case.context = case.context_for(3)
        case.begin("continuous", request)
        case.invoke("observe", case.observation(ref="native:dispatcher"))
        old = case.state()["scope_authority"]
        old_execution = case.execution_input()
        old_execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        old_execution["scope"]["owned_paths"] = ["impl.py"]
        old_execution["authorization"]["scope_digest"] = entry.digest(old_execution["scope"])
        first = old["execution_plan"]["catalog"]["tickets"][0]
        first_step = first["plan"]["steps"][0]
        old_refs = [execution_plan.step_ref(first, first_step)]
        old_execution["semantic"].update(inline_plan=request["semantic"]["inline_plan"],
            plan_refs=old_refs, authority_revision=old["revision"],
            authority_digest=entry.digest(old), plan_digest=old["execution_plan"]["plan_digest"])
        old_prepared = case.invoke("allocation", {"allocation_id": "inline-v1", "operation": "prepare",
            "handoff": old_execution})
        self.assertEqual(old_prepared["next_action"]["operation"], "invoke-host", old_prepared)
        old_slot = case.state()["allocations"]["inline-v1"]
        old_bound = case.invoke("allocation", {"allocation_id": "inline-v1", "operation": "bind",
            "receipt": case.receipt(old_slot["record"], ref="native:inline-v1")})
        old_release = old_bound["next_action"]["result"]["write_release"]
        old_slot = case.state()["allocations"]["inline-v1"]
        old_payload = {"changed_paths": [], "file_hashes": {}, "tests": ["inline behavior checked"],
            "write_release": old_release, "plan_refs": old_refs,
            "plan_authority": case.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        case.invoke("allocation", {"allocation_id": "inline-v1", "operation": "receive",
            "receipt": case.receipt(old_slot["record"], "stopped", "result", ref="native:inline-v1"),
            "result": {"delivery_id": "inline-v1-result", "status": "completed", "payload": old_payload}})
        case.invoke("allocation", {"allocation_id": "inline-v1", "operation": "accept",
            "decision": {"reference": "dispatcher:inline-v1-accepted"}})
        old_record = case.state()["allocations"]["inline-v1"]["record"]
        report = {"revision_id": "inline-private-1", "expected_authority_revision": old["revision"],
            "expected_authority_digest": entry.digest(old), "controller_ref": "task",
            "classification": {"kind": "internal", "reference": "controller:inline-private-revision"},
            "report": {"ticket_ids": ["INLINE-1"], "step_ids": ["inline-step"],
                "reason": "Change private implementation order", "production_facts": "The requested behavior is unchanged",
                "contract_before": old["execution_plan"]["contract_digest"],
                "contract_after": old["execution_plan"]["contract_digest"],
                "dependency_impact": ["inline-step"], "verification_impact": ["V-inline"],
                "carry_forward": []}, "planning_paths": [], "inline_plan": copy.deepcopy(request["semantic"]["inline_plan"])}
        report["inline_plan"]["plan_version"] = 2
        report["inline_plan"]["steps"][0]["approach"].append("Use the revised private sequence")
        before = case.flow_git("rev-parse", "HEAD")
        stopping = case.invoke("prepare-plan-revision", report)
        self.assertEqual(stopping["next_action"]["operation"], "stop-host", stopping)
        self.assertNotIn("write_release", stopping["next_action"])
        prepared = case.invoke("observe", case.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared", prepared)
        release = prepared["next_action"]["write_release"]
        activated = case.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": release, "planning_commit": before})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        current = case.state()["scope_authority"]
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["execution_plan"]["original_scope_revision"],
                         old["execution_plan"]["original_scope_revision"])
        self.assertIsNone(current["execution_plan"]["plan_commit"])
        self.assertEqual(current["execution_plan"]["sources"][0]["input_digest"],
                         old["execution_plan"]["sources"][0]["input_digest"])
        self.assertNotEqual(current["execution_plan"]["sources"][0]["sha256"],
                            old["execution_plan"]["sources"][0]["sha256"])
        self.assertEqual(current["execution_plan"]["catalog"]["tickets"][0]["plan_version"], 2)
        self.assertEqual(case.flow_git("rev-parse", "HEAD"), before)
        with self.assertRaises(entry.PreparationError) as old_error:
            dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "receive", "record": old_record,
                "control": {"context": case.state()["control"]["context"], "discussion": None},
                "receipt": case.receipt(old_record, "stopped", "result", ref="native:inline-v1"),
                "result": {"delivery_id": "late-inline-v1", "status": "completed", "payload": old_payload}})
        self.assertEqual(old_error.exception.code, 'authority_missing')
        with self.assertRaises(entry.PreparationError) as old_attempt:
            dispatch.validate_allocation_receipt(old_record, 'receive',
                case.receipt(old_record, 'stopped', 'result', ref='native:inline-v1'),
                case.state()['control']['context'],
                {'delivery_id': 'late-inline-v1', 'status': 'completed', 'payload': old_payload})
        self.assertIn(old_attempt.exception.code, {'attempt_mismatch', 'execution_plan_stale'})
        execution = case.execution_input()
        execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        execution["scope"]["downstream"] = copy.deepcopy(current["scope"]["downstream"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = entry.digest(execution["scope"])
        step = current["execution_plan"]["catalog"]["tickets"][0]["plan"]["steps"][0]
        execution["semantic"].update(inline_plan=report["inline_plan"],
            plan_refs=[execution_plan.step_ref(current["execution_plan"]["catalog"]["tickets"][0], step)],
            authority_revision=2, authority_digest=entry.digest(current),
            plan_digest=current["execution_plan"]["plan_digest"])
        new = case.invoke("allocation", {"allocation_id": "inline-v2", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(new["next_action"]["operation"], "invoke-host", new)
        old_request = copy.deepcopy(execution)
        old_record = old["execution_plan"]["catalog"]["tickets"][0]
        old_request["semantic"]["plan_refs"][0] = execution_plan.step_ref(
            old_record, old_record["plan"]["steps"][0])
        stale = case.invoke("allocation", {"allocation_id": "inline-old", "operation": "prepare",
            "handoff": old_request})
        self.assertEqual(stale["error"]["code"], "execution_plan_stale", stale)


if __name__ == "__main__":
    unittest.main()
