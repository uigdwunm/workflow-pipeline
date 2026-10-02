"""Plan-bound allocation through the saved Controller, B handoff and Git seam."""
import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

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
        "schema_version": 1, "ticket_id": "INLINE-1", "plan_version": 1,
        "spec_ref": {"path": "inline", "section": "brief"},
        "contract_refs": ["standalone:objective"], "blocked_by": [],
        "verifications": [{"id": "V-inline", "behavior": "requested behavior is present",
            "seam": "observed Python output", "commands": ["python3 scripts/run_tests.py --test-file tests/runtime/test_plan_driven_dispatch.py"]}],
        "steps": [{"id": "inline-step", "target": {"path": "impl.py", "symbol": "run", "operation": "add"},
            "capability": "implement the brief", "approach": ["write the requested implementation"],
            "inputs": ["frozen brief"], "outputs": ["observable result"], "failures": ["invalid input is reported"],
            "depends_on": [], "verification_refs": ["V-inline"],
            "read_only": [requirement_path]}], "artifact_steps": []}


class PlanDrivenDispatchTests(unittest.TestCase):
    def existing_inline_request(self, path, source, symbol, approach, operation="modify"):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
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
        request.pop("inline_authority")
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

    def test_standalone_artifact_only_plan_prepares_a_real_agent_ref(self):
        case, request = self.existing_inline_request(
            "prompt.md", "# previous prompt\n", "unused", ["revise the prompt"])
        plan = request["semantic"]["inline_plan"]
        plan["steps"] = []
        plan["artifact_steps"] = [{"id": "prompt-artifact", "path": "prompt.md",
            "operation": "modify", "capability": "revise the prompt",
            "approach": "write the approved prompt text", "verification_refs": ["V-inline"]}]
        saved = handoff.handle(request)
        prepared = dispatch.handle({"protocol": handoff.PROTOCOL,
            "operation": "prepare", "handoff": saved,
            "control": {"context": case.context_for(3), "discussion": None}})
        self.assertEqual(prepared["status"], "prepared")
        authority = prepared["checkpoint"]["context"]["handoff_progress"]["execution_authority"]
        catalog = authority["execution_plan"]
        self.assertEqual(catalog["ordered_step_ids"], ["prompt-artifact"])
        ticket = catalog["tickets"][0]
        ref = {"ticket_id": ticket["ticket_id"], "step_id": "prompt-artifact",
            "plan_version": ticket["plan_version"], "source": ticket["source"],
            "step_digest": entry.digest(plan["artifact_steps"][0])}
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
                catalog = started["checkpoint"]["context"]["handoff_progress"]["execution_authority"]["execution_plan"]
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
                self.assertEqual(authority["execution_plan"]["tickets"][0]
                                 ["plan"]["steps"][0]["target"]["symbol"], symbol)

    def active_flow(self, *, generated=False, read_dependency=False):
        return revision_fixture.PlanRevisionProgressTests.new_active_flow(
            self, generated=generated, read_dependency=read_dependency)

    def execution_request(self, flow):
        state = flow.state()
        authority = state["scope_authority"]
        catalog = authority["execution_plan"]
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
            {"ticket_id": ticket["ticket_id"], "step_id": step_id,
             "plan_version": ticket["plan_version"], "source": ticket["source"],
             "step_digest": entry.digest(next(step for step in ticket["plan"]["steps"] if step["id"] == step_id))}
            for step_id in catalog["ordered_step_ids"]]
        request["semantic"]["authority_revision"] = authority["revision"]
        request["semantic"]["authority_digest"] = entry.digest(authority)
        request["semantic"]["plan_digest"] = catalog["plan_digest"]
        return request

    def test_current_group_from_activated_authority_prepares_with_generated_map(self):
        flow = self.active_flow(generated=True)
        request = self.execution_request(flow)
        response = flow.invoke("allocation", {"allocation_id": "group", "operation": "prepare", "handoff": request})
        self.assertIsNotNone(response.get("next_action"), response)
        self.assertEqual(response["next_action"]["operation"], "invoke-host", response)
        saved = flow.state()["allocations"]["group"]
        self.assertEqual(saved["record"]["handoff"]["semantic"]["plan_refs"], request["semantic"]["plan_refs"])
        self.assertEqual(saved["record"]["request"]["payload"]["current_scope_authority"]["revision"], 1)
        self.assertEqual(saved["record"]["request"]["payload"]["semantic"]["execution_plan"]["plan_digest"],
                         flow.state()["scope_authority"]["execution_plan"]["plan_digest"])
        self.assertIn("generated/impl_copy.py", saved["record"]["request"]["payload"]["generated_outputs"]["impl.py"])

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

        second = self.execution_request(flow)
        second['scope']['owned_paths'] = ['later.py']
        second['scope']['protected_paths'] = sorted([*second['scope']['protected_paths'], 'impl.py'])
        second['authorization']['scope_digest'] = entry.digest(second['scope'])
        second['semantic']['plan_refs'] = second['semantic']['plan_refs'][1:]
        prepared = flow.invoke('allocation', {'allocation_id': 'later', 'operation': 'prepare',
            'handoff': second})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = flow.state()['allocations']['later']
        bound = flow.invoke('allocation', {'allocation_id': 'later', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:later')})
        self.assertTrue(bound['next_action']['result']['write_release'])

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

    def test_dependency_requires_stopped_exact_current_result(self):
        flow = self.active_flow()
        request = self.execution_request(flow)
        authority = flow.state()['scope_authority']
        refs = request['semantic']['plan_refs']
        current = flow.state()['control']['context']['handoff_progress']['plan_authority']
        first = {'state': 'accepted', 'stopped': False, 'plan_authority': current,
                 'plan_refs': [refs[0]], 'git_result_snapshot': {'impl.py': 'saved'}}
        with self.assertRaises(entry.PreparationError) as error:
            execution_plan.validate_plan_refs(authority['execution_plan'], [refs[1]],
                ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(error.exception.code, 'execution_plan_stale')
        first['stopped'] = True
        first['plan_refs'][0] = {**refs[0], 'step_digest': 'historical-step'}
        with self.assertRaises(entry.PreparationError) as error:
            execution_plan.validate_plan_refs(authority['execution_plan'], [refs[1]],
                ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(error.exception.code, 'execution_plan_stale')
        first['plan_refs'][0] = refs[0]
        group = execution_plan.validate_plan_refs(authority['execution_plan'], [refs[1]],
            ['impl.py'], request['scope']['protected_paths'], [first], authority)
        self.assertEqual(group['ordered_step_ids'], ['FP1-02'])
        first['plan_authority'] = {**current, 'revision': current['revision'] - 1}
        with self.assertRaises(entry.PreparationError):
            execution_plan.validate_plan_refs(authority['execution_plan'], [refs[1]],
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
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
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
        saved = handoff.handle(request)
        dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved,
                         "control": {"context": flow.state()["control"]["context"], "discussion": None}})
        current = flow.invoke("allocation", {"allocation_id": "current-group", "operation": "prepare",
            "handoff": request})
        self.assertIsNotNone(current.get("next_action"), current)
        self.assertEqual(current["next_action"]["operation"], "invoke-host", current)
        payload = flow.state()["allocations"]["current-group"]["record"]["request"]["payload"]
        self.assertEqual(payload["current_scope_authority"]["revision"], 2)
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
        slot = flow.state()['allocations']['current-group']
        result_payload = {'changed_paths': ['impl.py'],
            'file_hashes': {'impl.py': hashlib.sha256(implementation).hexdigest()},
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
        flow.flow_git('add', 'impl.py')
        flow.flow_git('commit', '-qm', 'revised staged implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')

        def mark_current_reviewable(_candidate, fixture_execution=False):
            flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
            checkpoint = flow.state()['control']['context']['handoff_progress']
            checks = transfer_fixture.checks_for(checkpoint['validation_plan']['review_required'], candidate)
            result = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
                'dispatcher_ref': 'native:dispatcher',
                'attempt': flow.state()['control']['context']['carrier']['attempt'],
                'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
                'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
                'plan_authority': checkpoint['plan_authority'], 'checks': checks},
                'receipt': {'adapter': 'fixture', 'call_ref': 'focused-staged',
                    'response_ref': 'focused-staged-result', 'raw': {'checks': checks,
                        'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}})
            self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'],
                             'reviewable', result)
            return {'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
                    'plan_authority': checkpoint['plan_authority'], 'checks': checks}

        flow.mark_reviewable = mark_current_reviewable
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
        request.pop("inline_authority")
        saved = handoff.handle(request)
        payload = handoff.render(saved)["payload"]
        self.assertEqual(payload["execution_plan"]["tickets"][0]["plan"]["ticket_id"], "INLINE-1")
        self.assertEqual(payload["current_scope_authority"]["kind"], "inline")

    def test_standalone_inline_group_allocates_under_original_controller(self):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority")
        case.context = case.context_for(3)
        case.begin("continuous", request)
        case.invoke("observe", case.observation(ref="native:dispatcher"))
        authority = case.state()["handoff"]["inline_authority"]
        execution = case.execution_input()
        execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = entry.digest(execution["scope"])
        record = authority["execution_plan"]["tickets"][0]
        step = record["plan"]["steps"][0]
        execution["semantic"].update(inline_plan=request["semantic"]["inline_plan"],
            plan_refs=[{"ticket_id": record["ticket_id"], "step_id": step["id"],
                "plan_version": record["plan_version"], "source": record["source"],
                "step_digest": entry.digest(step)}],
            authority_revision=authority["revision"], authority_digest=entry.digest(authority),
            plan_digest=authority["execution_plan"]["plan_digest"])
        prepared = case.invoke("allocation", {"allocation_id": "inline-group", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)

    def test_inline_revision_switches_current_plan_without_document_commit(self):
        case = progress_fixture.ProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        request = case.input_for(3)
        request["semantic"]["inline_plan"] = inline_plan(case.requirement_path)
        request.pop("inline_authority")
        case.context = case.context_for(3)
        case.begin("continuous", request)
        case.invoke("observe", case.observation(ref="native:dispatcher"))
        old = case.state()["scope_authority"]
        old_execution = case.execution_input()
        old_execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        old_execution["scope"]["owned_paths"] = ["impl.py"]
        old_execution["authorization"]["scope_digest"] = entry.digest(old_execution["scope"])
        first = old["execution_plan"]["tickets"][0]
        first_step = first["plan"]["steps"][0]
        old_refs = [{"ticket_id": first["ticket_id"], "step_id": first_step["id"],
            "plan_version": first["plan_version"], "source": first["source"],
            "step_digest": entry.digest(first_step)}]
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
        self.assertEqual(current["execution_plan"]["tickets"][0]["plan_version"], 2)
        self.assertEqual(case.flow_git("rev-parse", "HEAD"), before)
        with self.assertRaises(entry.PreparationError) as old_error:
            dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "receive", "record": old_record,
                "control": {"context": case.state()["control"]["context"], "discussion": None},
                "receipt": case.receipt(old_record, "stopped", "result", ref="native:inline-v1"),
                "result": {"delivery_id": "late-inline-v1", "status": "completed", "payload": old_payload}})
        self.assertIn(old_error.exception.code, {"attempt_mismatch", "execution_plan_stale"})
        execution = case.execution_input()
        execution["scope"] = copy.deepcopy(case.state()["handoff"]["scope"])
        execution["scope"]["downstream"] = copy.deepcopy(current["scope"]["downstream"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = entry.digest(execution["scope"])
        step = current["execution_plan"]["tickets"][0]["plan"]["steps"][0]
        execution["semantic"].update(inline_plan=report["inline_plan"],
            plan_refs=[{"ticket_id": "INLINE-1", "step_id": step["id"], "plan_version": 2,
                        "source": current["execution_plan"]["tickets"][0]["source"],
                        "step_digest": entry.digest(step)}],
            authority_revision=2, authority_digest=entry.digest(current),
            plan_digest=current["execution_plan"]["plan_digest"])
        new = case.invoke("allocation", {"allocation_id": "inline-v2", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(new["next_action"]["operation"], "invoke-host", new)
        old_request = copy.deepcopy(execution)
        old_record = old["execution_plan"]["tickets"][0]
        old_request["semantic"]["plan_refs"][0].update(plan_version=1, source=old_record["source"],
            step_digest=entry.digest(old_record["plan"]["steps"][0]))
        stale = case.invoke("allocation", {"allocation_id": "inline-old", "operation": "prepare",
            "handoff": old_request})
        self.assertEqual(stale["error"]["code"], "execution_plan_stale", stale)


if __name__ == "__main__":
    unittest.main()
