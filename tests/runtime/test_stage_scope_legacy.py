"""Narrow legacy Stage-2 takeover against an actual v12/v7 fixture.

The old package and checkpoint are generated from the fixed repository commit
in a temporary directory. No user's runtime record or installed package is read.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import workflow_progress as progress
import stage_handoff as handoff
import stage_scope


ROOT = Path(__file__).resolve().parents[2]
OLD_REVISION = "ba2a1fa"
OLD_PREPARATION_V3_REVISION = "973685e7ae0e3077bfdf9910be06136e95e06762"


# Build the historical record with its own runtime in a separate interpreter.
# The fixture's temporary directories remain under the test's single cleanup
# root, while its registered worktree keeps all Git common-dir links valid.
OLD_FIXTURE_SCRIPT = r'''
import json
from pathlib import Path
import sys
import tempfile

source, scratch, pin_path, metadata_path = map(Path, sys.argv[1:5])
variant = sys.argv[5]
sys.path.insert(0, str(source / 'tests/runtime'))
sys.path.insert(0, str(source / 'src/shared/scripts'))
sys.path.insert(0, str(source / 'src/stages/guided-implementation/scripts'))

class PreservedTemporaryDirectory:
    def __init__(self):
        self.name = tempfile.mkdtemp(dir=str(scratch))
    def cleanup(self):
        pass

tempfile.TemporaryDirectory = PreservedTemporaryDirectory
import test_workflow_progress as fixture_module
import workflow_progress as old_progress
import workflow as old_runner

fixture = fixture_module.ProgressTests(methodName='runTest')
fixture.setUp()
pins = json.loads(pin_path.read_text())
pin = pins['solution-design']
fixture.package.clear()
fixture.package.update(pin)
fixture.input = fixture.input_for(2)
if variant == 'empty':
    fixture.input['scope']['implementation_paths'] = []
    fixture.input['scope']['closure_paths'] = []
else:
    assert variant in {'missing-test', 'prepared-stage3'}
fixture.input['authorization']['scope_digest'] = fixture_module.transfer.entry.digest(fixture.input['scope'])
fixture.context = fixture.context_for(2)
accepted_view = fixture.finish_design('continuous')
assert accepted_view['status'] == 'accepted' and accepted_view['downstream_ready'] is True
old_state = fixture.state()
assert old_state['protocol'] == 'workflow-progress-v12'
assert old_state['accepted']['handoff']['protocol'] == 'workflow-stage-transfer-v7'
assert old_state['control']['context']['schema_version'] == 4
old_progress.validate_state(old_state)

confirmed = {**{key: fixture.binding[key] for key in
    ('repository', 'worktree', 'git_common_dir', 'target_branch')},
    'controller_ref': 'task', 'flow_mode': 'continuous',
    'frozen_requirement': {**fixture.frozen, 'path': fixture.frozen['absolute_path']},
    'packages': {'runner': pins['guided-implementation'],
        'stage2': pins['solution-design'], 'stage3': pins['guided-implementation'],
        'stage4': pins['change-closure']}}
outer = dict(old_runner._new_state(confirmed))
outer.update(status='active', current_stage='stage2',
    sessions={'stage2': fixture.request['host']['thread_id']},
    stage_results={'stage2': accepted_view['completion']['stage_result']},
    launch={'stage': 'stage2', 'state': 'completed_turn', 'turn': 1})
if variant == 'prepared-stage3':
    prepared = fixture_module.transfer.handoff.handle(
        fixture.input_for(3, old_state['accepted']))
    assert prepared['stage'] == 3 and prepared['protocol'] == 'workflow-stage-transfer-v7'
    outer['stage3_prepared'] = prepared
outer[old_progress.KEY] = old_state
old_progress.atomic_save(fixture.checkpoint, outer)
assert old_progress.read_record(fixture.checkpoint)['version'] == 6
raw = fixture.checkpoint.read_bytes()
metadata = {'checkpoint': str(fixture.checkpoint.resolve()),
    'repository': str(fixture.root), 'worktree': str(fixture.flow),
    'accepted_digest': old_state['accepted']['digest'],
    'controller_ref': old_state['handoff']['controller_ref'],
    'planning_commit': old_state['accepted']['payload']['planning_commit'],
    'planning_merge_commit': old_state['accepted']['payload']['planning_merge_commit'],
    'host_evidence': {'adapter': 'app-server', 'snapshot': old_state.get('lifecycle_snapshot')},
    'old_pin': pin, 'variant': variant,
    'sha256': __import__('hashlib').sha256(raw).hexdigest()}
metadata_path.write_text(json.dumps(metadata, sort_keys=True))
'''


def historical_fixture(revision, scratch, variant="empty"):
    source = scratch / "old-source"
    source.mkdir()
    archived = subprocess.run(["git", "-C", str(ROOT), "archive", revision],
                              capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archived)) as tar:
        tar.extractall(source)
    packages = scratch / "old-packages"
    subprocess.run([sys.executable, str(source / "scripts/build_skills.py"),
                    "--output", str(packages)], capture_output=True, check=True)
    pins = {}
    for name in ("design-discussion", "problem-framing", "solution-design",
                 "guided-implementation", "change-closure"):
        package = packages / name
        manifest = json.loads((package / "package.json").read_text())
        pins[name] = {"name": name, "stage": manifest["stage"],
            "entry": str(package / "SKILL.md"), "root": str(package),
            "format_version": manifest["format_version"], "release": manifest["release"],
            "bundle_digest": manifest["bundle_digest"],
            "compatibility_key": manifest["compatibility_key"]}
    pin_path = scratch / "old-pin.json"
    pin_path.write_text(json.dumps(pins))
    metadata_path = scratch / "fixture.json"
    generated = subprocess.run([sys.executable, "-c", OLD_FIXTURE_SCRIPT,
        str(source), str(scratch), str(pin_path), str(metadata_path), variant],
        cwd=source, capture_output=True, text=True)
    if generated.returncode:
        raise AssertionError("old fixture construction failed:\n" + generated.stderr[-5000:])
    return json.loads(metadata_path.read_text())


class LegacyScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            OLD_REVISION + "^{commit}"], capture_output=True)
        if available.returncode:
            raise unittest.SkipTest("historical ba2a1fa runtime is absent from this checkout")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.scratch = Path(cls.temporary.name).resolve()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.metadata = historical_fixture(OLD_REVISION, cls.scratch)
        cls.checkpoint = Path(cls.metadata["checkpoint"])
        cls.original_bytes = cls.checkpoint.read_bytes()
        cls.original = json.loads(cls.original_bytes)

    def setUp(self):
        # Every case starts from the exact historical bytes and original path.
        self.checkpoint.write_bytes(self.original_bytes)

    def recovery_input(self, *, action="inspect"):
        return {"action": action, "recovery_id": "legacy-scope-1",
                "original_checkpoint_sha256": self.metadata["sha256"],
                "original_accepted_digest": self.metadata["accepted_digest"],
                "controller_ref": self.metadata["controller_ref"],
                "host_evidence": self.metadata["host_evidence"]}

    def recover(self, data):
        return progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
            "operation": "scope-recover-legacy", "expected_revision": 0, "data": data})

    def new_package_pins(self):
        cache = getattr(self.__class__, "_pins_by_scratch", {})
        if str(self.scratch) in cache:
            return cache[str(self.scratch)]
        output = self.scratch / "new-packages"
        subprocess.run([sys.executable, str(ROOT / "scripts/build_skills.py"),
                        "--output", str(output)], capture_output=True, check=True)
        pins = {}
        for name in ("design-discussion", "problem-framing", "solution-design",
                     "guided-implementation", "change-closure"):
            package = output / name
            manifest = json.loads((package / "package.json").read_text())
            pins[name] = {"name": name, "stage": manifest["stage"],
                "entry": str(package / "SKILL.md"), "root": str(package),
                "format_version": manifest["format_version"], "release": manifest["release"],
                "bundle_digest": manifest["bundle_digest"],
                "compatibility_key": manifest["compatibility_key"]}
        cache[str(self.scratch)] = pins
        self.__class__._pins_by_scratch = cache
        return pins

    def takeover_inputs(self):
        """New evidence is built independently of the immutable old accepted result."""
        old = self.original[progress.KEY]
        binding = old["handoff"]["binding"]
        pins = self.new_package_pins()
        source = old["handoff"]["requirement"]
        registry = {"source": "host-current-skills", "entries": [
            {"name": name, "entry": pins[name]["entry"], "source": "legacy-test-host", "enabled": True}
            for name in ("solution-design", "guided-implementation", "change-closure")]}
        context = {"project_path": binding["repository"], "project_id": "project",
                   "controller_ref": self.metadata["controller_ref"],
                   "receipt": "host:new-registration", "registry_digest": entry.digest(registry)}
        registry_input = self.scratch / "new-registry.json"
        registry_input.write_text(json.dumps({"registry": registry, "registry_context": context}))
        _, registration = entry.registration_inputs({"registry_input": str(registry_input),
            "host": {"project_id": "project", "controller_ref": self.metadata["controller_ref"]},
            "target": {"binding": {"repository": binding["repository"],
                                    "git_common_dir": binding["git_common_dir"]}}},
            entry.repository_facts(binding["repository"]))
        from test_stage_transfer import configuration
        selections = {stage: {"model": "fixture-model", "reasoning_effort": "high",
            "selection_input": configuration("scripted-carrier")}
            for stage in ("stage2", "stage3", "stage4")}
        roots = [binding["repository"], binding["worktree"], binding["git_common_dir"]]
        host = {"transport": "app-server-stdio", "cli_version": "codex-cli fixture",
            "source": {"kind": "controller-current-config", "controller_ref": self.metadata["controller_ref"],
                       "receipt": "host:new-effective-config"},
            "thread": {"approvalPolicy": "never", "sandbox": "read-only", "config": {},
                       "runtimeWorkspaceRoots": roots},
            "effective": {"approvalPolicy": "never", "sandbox": {"type": "readOnly"},
                          "runtimeWorkspaceRoots": roots}}
        workspace = {"controller_ref": self.metadata["controller_ref"],
                     "reference": "controller:legacy-scope", "worktree": binding["worktree"],
                     "receipts": [{"tool": "fixture:workspace-selection",
                                   "request": {"worktree": binding["worktree"]},
                                   "response": {"worktree": binding["worktree"], "status": "ready"}}]}
        confirmed = {"controller_ref": self.metadata["controller_ref"],
            "frozen_requirement": {"path": source["absolute_path"], "commit": source["commit"],
                                   "sha256": source["sha256"]},
            "requirement": copy.deepcopy(source),
            **{key: binding[key] for key in ("repository", "worktree", "git_common_dir", "target_branch")},
            "authority_scope": {"allowed_paths": ["CHANGELOG.md", "impl.py"]},
            "run_record": str(self.checkpoint), "stages": selections,
            "packages": {"runner": pins["guided-implementation"],
                         "stage2": pins["solution-design"],
                         "stage3": pins["guided-implementation"],
                         "stage4": pins["change-closure"]},
            "flow_mode": "continuous", "host": host,
            "registry_input": str(registry_input), "registration_context": registration,
            "workspace_evidence": workspace}
        basis = {"commit": self.metadata["planning_commit"], "path": "docs/spec.md",
                 "section": "approved design"}
        manifest = [{"path": "CHANGELOG.md", "owner": "closure", "kind": "document",
                     "operation": "add", "basis": basis},
                    {"path": "impl.py", "owner": "implementation", "kind": "source",
                     "operation": "add", "basis": basis}]
        return {**self.recovery_input(action="prepare-takeover"), "manifest": manifest,
            "implementation_required": True,
            "decision": {"kind": "in-requirement-refinement", "reference": "controller:legacy-scope"},
            "new_packages": pins, "new_confirmed": confirmed,
            "workspace_evidence": workspace}

    def stage_three_request(self, record, inputs):
        from test_stage_transfer import configuration
        from test_workflow_control import plan_for
        binding = record["binding"]
        original = self.original[progress.KEY]
        source = original["handoff"]["requirement_identity"]
        entry_request = {"protocol": entry.PROTOCOL, "operation": "resolve",
            "stage": 3, "action": "entry",
            "host": {"project_path": binding["worktree"], "project_id": "project",
                     "thread_id": "task", "controller_ref": "task", "role": "controller",
                     "source_ref": None, "receipt": "host:recovered-stage3",
                     "supported_configurations": [{"model": "fixture-model", "reasoning_effort": "high"}]},
            "source": {"kind": "frozen", "path": source["path"]},
            "target": {"kind": "flow", "binding": binding},
            "registry_input": inputs["new_confirmed"]["registry_input"]}
        settings = {"protocol": "thread-settings-v5", "source": "fixture-runtime",
                    "thread_id": "task", "model": "fixture-model",
                    "reasoning_effort": "high", "turn_id": "turn-1"}
        with patch.object(entry.os, "getcwd", return_value=binding["worktree"]), \
                patch.object(entry.thread_settings, "resolve_current_thread_settings", return_value=settings):
            expected = entry.resolve(entry_request)
        effective = record["supplemental_authority"]["scope"]
        scope = {**copy.deepcopy(effective),
            "baseline": record["bridge"]["payload"]["planning_merge_commit"],
            "owned_paths": copy.deepcopy(effective["implementation_paths"]),
            "protected_paths": sorted(set(effective["protected_paths"] +
                                          record["bridge"]["payload"]["planning_paths"]))}
        return {"protocol": handoff.PROTOCOL, "operation": "prepare",
            "entry": entry_request, "expected_entry": expected,
            "stage": 3, "role": "implementation-dispatcher",
            "target": {"repository": binding["repository"], "branch": binding["target_branch"]},
            "delivery": None, "binding": binding,
            "authorization": {"reference": "controller:legacy-scope", "flow_mode": "continuous",
                              "scope_digest": entry.digest(scope)},
            "configuration": configuration("implementation-dispatcher"),
            "semantic": {"objective": "implement the approved file", "testing_basis": "real CLI",
                         "completion_criteria": ["verified output"], "constraints": ["no remote writes"],
                         "validation_plan": plan_for(Path(binding["worktree"]))}}

    def authorized_standalone(self):
        inputs = self.takeover_inputs()
        standalone = {progress.KEY: copy.deepcopy(self.original[progress.KEY])}
        self.checkpoint.write_text(json.dumps(standalone, sort_keys=True))
        prepared = copy.deepcopy(inputs)
        prepared["original_checkpoint_sha256"] = hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()
        prepared.pop("new_confirmed")
        self.recover(prepared)
        self.recover({"action": "activate", "recovery_id": "legacy-scope-1",
            "original_checkpoint_sha256": prepared["original_checkpoint_sha256"],
            "controller_ref": self.metadata["controller_ref"],
            "host_evidence": self.metadata["host_evidence"]})
        current = json.loads(self.checkpoint.read_bytes())
        return inputs, prepared, current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]

    def recovered_stage_three_start(self, record, inputs):
        native = copy.deepcopy(self.metadata["host_evidence"])
        native["snapshot"]["instance"] = "new-fixture-host"
        native["snapshot"]["events"] = []
        native["snapshot"]["sequence"] = 0
        for page in native["snapshot"]["pages"]:
            page["response"]["data"] = []
        current = json.loads(self.checkpoint.read_bytes())
        return {"protocol": progress.PROTOCOL, "operation": "start",
            "expected_revision": current[progress.KEY]["revision"],
            "data": {"handoff": self.stage_three_request(record, inputs), "native_host": native,
                     "workspace_evidence": record["workspace_evidence"]}}

    def recovered_host_patches(self, record):
        settings = {"protocol": "thread-settings-v5", "source": "fixture-runtime",
                    "thread_id": "task", "model": "fixture-model",
                    "reasoning_effort": "high", "turn_id": "turn-1"}
        return (patch.object(entry.os, "getcwd", return_value=record["binding"]["worktree"]),
                patch.object(entry.thread_settings, "resolve_current_thread_settings", return_value=settings))

    def test_fixture_is_exact_old_runtime_with_real_git_and_package(self):
        old = self.original[progress.KEY]
        self.assertEqual(old["protocol"], "workflow-progress-v12")
        self.assertEqual(old["accepted"]["handoff"]["protocol"], "workflow-stage-transfer-v7")
        self.assertEqual(old["control"]["context"]["schema_version"], 4)
        self.assertEqual(old["handoff"]["scope"]["implementation_paths"], [])
        self.assertEqual(old["handoff"]["scope"]["closure_paths"], [])
        self.assertEqual(self.original["version"], 6)
        self.assertEqual(self.metadata["sha256"], hashlib.sha256(self.original_bytes).hexdigest())
        self.assertEqual(self.metadata["old_pin"]["bundle_digest"],
                         json.loads((self.scratch / "old-packages/solution-design/package.json").read_text())["bundle_digest"])
        self.assertEqual(old["handoff"]["expected_entry"]["packages"]["problem-framing"],
                         self.metadata["old_pin"])
        self.assertTrue(Path(self.metadata["repository"]).is_dir())
        self.assertTrue(Path(self.metadata["worktree"]).is_dir())
        self.assertEqual(old["accepted"]["payload"]["planning_commit"], self.metadata["planning_commit"])
        self.assertIsNotNone(self.metadata["host_evidence"]["snapshot"])

    def test_supplemental_runner_input_verifies_old_frozen_source_in_recovery_mode(self):
        inputs = self.takeover_inputs()
        script = Path(inputs["new_packages"]["guided-implementation"]["root"]) / "scripts/workflow.py"
        spec = importlib.util.spec_from_file_location("supplemental_legacy_runner", script)
        runner = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(runner)
        normalized = runner.validate_confirmed(inputs["new_confirmed"],
                                               restoring=True, legacy_recovery=True)
        self.assertEqual(normalized["repository"], self.metadata["repository"])
        self.assertEqual(normalized["run_record"], str(self.checkpoint))
        self.assertEqual(normalized["packages"]["runner"], inputs["new_packages"]["guided-implementation"])
        self.assertEqual(self.checkpoint.read_bytes(), self.original_bytes)

    def test_standalone_takeover_preserves_exact_old_bytes_and_one_current(self):
        standalone = {progress.KEY: copy.deepcopy(self.original[progress.KEY])}
        self.checkpoint.write_text(json.dumps(standalone, sort_keys=True))
        before = self.checkpoint.read_bytes()
        old_head = subprocess.run(["git", "-C", self.metadata["repository"],
            "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        inputs = self.takeover_inputs()
        inputs["original_checkpoint_sha256"] = hashlib.sha256(before).hexdigest()
        inputs.pop("new_confirmed")
        result = self.recover(inputs)
        self.assertIn(result["status"], {"takeover-pending", "authorized"}, result)
        current = json.loads(self.checkpoint.read_bytes())
        self.assertNotIn("version", current)
        self.assertEqual(current[progress.KEY]["protocol"], progress.PROTOCOL)
        recovery = current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
        self.assertEqual(base64.b64decode(recovery["original_bytes_base64"]), before)
        self.assertEqual(recovery["original_accepted"], standalone[progress.KEY]["accepted"])
        self.assertEqual(recovery["supplemental_authority"]["scope"]["implementation_paths"], ["impl.py"])
        after = self.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as pending_error:
            progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
                "operation": "start", "expected_revision": current[progress.KEY]["revision"],
                "data": {}})
        self.assertEqual(pending_error.exception.code, "legacy_takeover_pending")
        self.assertEqual(self.checkpoint.read_bytes(), after)
        replay = self.recover(inputs)
        self.assertTrue(replay["acknowledged"], replay)
        self.assertEqual(self.checkpoint.read_bytes(), after)
        different = copy.deepcopy(inputs)
        different["decision"]["reference"] = "controller:another-scope"
        with self.assertRaises(entry.PreparationError) as raised:
            self.recover(different)
        self.assertEqual(raised.exception.code, "scope_conflict")
        self.assertEqual(self.checkpoint.read_bytes(), after)
        new_head = subprocess.run(["git", "-C", self.metadata["repository"],
            "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(new_head, old_head)
        activation_input = {"action": "activate", "recovery_id": "legacy-scope-1",
            "original_checkpoint_sha256": inputs["original_checkpoint_sha256"],
            "controller_ref": self.metadata["controller_ref"],
            "host_evidence": self.metadata["host_evidence"]}
        activated = self.recover(activation_input)
        self.assertEqual(activated["state"], "authorized", activated)
        self.assertTrue(activated["downstream_ready"], activated)
        active = json.loads(self.checkpoint.read_bytes())
        self.assertEqual(active[progress.KEY]["scope_recoveries"]["legacy-scope-1"]["state"], "authorized")
        self.assertEqual(progress.legacy_scope_readiness(active[progress.KEY], self.checkpoint),
                         activated["readiness"])
        active_bytes = self.checkpoint.read_bytes()
        self.assertTrue(self.recover(activation_input)["acknowledged"])
        self.assertEqual(self.checkpoint.read_bytes(), active_bytes)

    def test_runner_six_takeover_creates_valid_runner_eight_without_reusing_old_session(self):
        before = self.checkpoint.read_bytes()
        inputs = self.takeover_inputs()
        result = self.recover(inputs)
        self.assertEqual(result["state"], "takeover-pending", result)
        current = json.loads(self.checkpoint.read_bytes())
        self.assertEqual(current["version"], 8)
        self.assertEqual(current["status"], "recovery_pending")
        self.assertEqual(current["sessions"], {})
        self.assertEqual(current["stage_results"], {})
        recovery = current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
        self.assertEqual(base64.b64decode(recovery["original_bytes_base64"]), before)
        self.assertEqual(recovery["original_accepted"], self.original[progress.KEY]["accepted"])
        self.assertEqual(recovery["supplemental_authority"]["scope"]["implementation_paths"], ["impl.py"])
        script = Path(inputs["new_packages"]["guided-implementation"]["root"]) / "scripts/workflow.py"
        spec = importlib.util.spec_from_file_location("taken_over_runner", script)
        runner = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(runner)
        runner._validate_record(current)
        activation = self.recover({"action": "activate", "recovery_id": "legacy-scope-1",
            "original_checkpoint_sha256": self.metadata["sha256"],
            "controller_ref": self.metadata["controller_ref"],
            "host_evidence": self.metadata["host_evidence"]})
        self.assertEqual(activation["state"], "authorized", activation)
        active = json.loads(self.checkpoint.read_bytes())
        self.assertEqual(active["status"], "active")
        self.assertEqual(active[progress.KEY]["scope_recoveries"]["legacy-scope-1"]["state"], "authorized")
        runner._validate_record(active)
        prompt = runner._stage_prompt(active, "stage3", None, False)
        self.assertIn("legacy_recovery", prompt)

    def test_standalone_authorization_is_consumed_by_one_real_stage_three_start(self):
        inputs = self.takeover_inputs()
        standalone = {progress.KEY: copy.deepcopy(self.original[progress.KEY])}
        self.checkpoint.write_text(json.dumps(standalone, sort_keys=True))
        inputs["original_checkpoint_sha256"] = hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()
        prepared = copy.deepcopy(inputs)
        prepared.pop("new_confirmed")
        self.recover(prepared)
        self.recover({"action": "activate", "recovery_id": "legacy-scope-1",
            "original_checkpoint_sha256": prepared["original_checkpoint_sha256"],
            "controller_ref": self.metadata["controller_ref"],
            "host_evidence": self.metadata["host_evidence"]})
        authorized = json.loads(self.checkpoint.read_bytes())
        record = authorized[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
        handoff_request = self.stage_three_request(record, inputs)
        native = copy.deepcopy(self.metadata["host_evidence"])
        native["snapshot"]["instance"] = "new-fixture-host"
        native["snapshot"]["events"] = []
        native["snapshot"]["sequence"] = 0
        for page in native["snapshot"]["pages"]:
            page["response"]["data"] = []
        start = {"protocol": progress.PROTOCOL, "operation": "start",
            "expected_revision": authorized[progress.KEY]["revision"],
            "data": {"handoff": handoff_request, "native_host": native,
                     "workspace_evidence": prepared["workspace_evidence"]}}
        settings = {"protocol": "thread-settings-v5", "source": "fixture-runtime",
                    "thread_id": "task", "model": "fixture-model",
                    "reasoning_effort": "high", "turn_id": "turn-1"}
        with patch.object(entry.os, "getcwd", return_value=record["binding"]["worktree"]), \
                patch.object(entry.thread_settings, "resolve_current_thread_settings", return_value=settings):
            first = progress.handle(self.checkpoint, start)
            self.assertEqual(first["next_action"]["operation"], "invoke-host", first)
            current = json.loads(self.checkpoint.read_bytes())
            self.assertEqual(current[progress.KEY]["stage"], 3)
            self.assertEqual(current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]["state"], "consumed")
            self.assertEqual(current[progress.KEY]["handoff"]["predecessor"], record["bridge"])
            self.assertEqual(current[progress.KEY]["handoff"]["binding"]["worktree"], self.metadata["worktree"])
            self.assertEqual(current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                             standalone[progress.KEY]["accepted"])
            after = self.checkpoint.read_bytes()
            replay = progress.handle(self.checkpoint, start)
            self.assertTrue(replay["acknowledged"], replay)
            self.assertEqual(self.checkpoint.read_bytes(), after)

    def test_copied_checkpoint_after_takeover_cannot_activate_or_start(self):
        standalone = {progress.KEY: copy.deepcopy(self.original[progress.KEY])}
        self.checkpoint.write_text(json.dumps(standalone, sort_keys=True))
        inputs = self.takeover_inputs()
        inputs["original_checkpoint_sha256"] = hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()
        inputs.pop("new_confirmed")
        self.recover(inputs)
        original_current = self.checkpoint.read_bytes()
        copied = self.scratch / "copied-after-takeover.json"
        copied.write_bytes(original_current)
        before = copied.read_bytes()
        with self.assertRaises(entry.PreparationError) as raised:
            progress.handle(copied, {"protocol": progress.PROTOCOL,
                "operation": "scope-recover-legacy", "expected_revision": 0,
                "data": {"action": "activate", "recovery_id": "legacy-scope-1",
                    "original_checkpoint_sha256": inputs["original_checkpoint_sha256"],
                    "controller_ref": self.metadata["controller_ref"],
                    "host_evidence": self.metadata["host_evidence"]}})
        self.assertEqual(raised.exception.code, "legacy_recovery_changed")
        self.assertEqual(copied.read_bytes(), before)
        self.assertEqual(self.checkpoint.read_bytes(), original_current)

    def test_interrupted_stage_three_prepare_resumes_one_original_dispatch(self):
        inputs, _, record = self.authorized_standalone()
        start = self.recovered_stage_three_start(record, inputs)
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            with patch.object(progress.dispatch, "handle", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    progress.handle(self.checkpoint, start)
            interrupted = json.loads(self.checkpoint.read_bytes())[progress.KEY]
            self.assertEqual(interrupted["stage"], 3)
            self.assertEqual(interrupted["step"], "prepare-dispatch")
            self.assertEqual(interrupted["scope_recoveries"]["legacy-scope-1"]["state"], "consumed")
            self.assertEqual(interrupted["recovery_start_input"], start["data"])
            self.assertIsNone(interrupted["dispatch"])
            with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as dispatch_once:
                resumed = progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
                    "operation": "advance", "expected_revision": interrupted["revision"], "data": {}})
            self.assertEqual(dispatch_once.call_count, 1)
            self.assertEqual(resumed["next_action"]["operation"], "invoke-host", resumed)
            current = json.loads(self.checkpoint.read_bytes())[progress.KEY]
            handoff.verify(current["handoff"])
            progress.dispatch.record_for({"record": current["dispatch"], "control": current["control"]})
            after = self.checkpoint.read_bytes()
            with patch.object(progress.dispatch, "handle", side_effect=AssertionError("duplicate dispatch")):
                replay = progress.handle(self.checkpoint, start)
            self.assertTrue(replay["acknowledged"], replay)
            self.assertEqual(self.checkpoint.read_bytes(), after)

    def test_recovered_stage_three_revision_retains_old_snapshot_and_bridge(self):
        inputs, _, record = self.authorized_standalone()
        start = self.recovered_stage_three_start(record, inputs)
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            progress.handle(self.checkpoint, start)
        from test_workflow_progress import ProgressTests
        observer = ProgressTests(methodName="runTest")
        observer.checkpoint = self.checkpoint
        observer.host_response_sequence = 0
        current = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        ready = observer.observation(ref="native:dispatcher")
        progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
            "operation": "observe", "expected_revision": current["revision"], "data": ready})
        current = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertEqual(current["dispatch"]["status"], "bound")
        authority = current["scope_authority"]
        old_recovery = copy.deepcopy(current["scope_recoveries"]["legacy-scope-1"])
        payload = old_recovery["bridge"]["payload"]
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
            "operation": "add", "basis": {"commit": payload["planning_commit"],
            "path": "docs/spec.md", "section": "approved design"}})
        revision = {"revision_id": "legacy-add-test-1", "expected_scope_revision": 1,
            "previous_scope_digest": entry.digest(authority["scope"]),
            "requirement_identity": copy.deepcopy(old_recovery["requirement_identity"]),
            "binding": copy.deepcopy(current["handoff"]["binding"]),
            "planning_commit": payload["planning_commit"],
            "planning_merge_commit": payload["planning_merge_commit"],
            "candidate_commit": payload["planning_commit"],
            "old_manifest": copy.deepcopy(authority["scope"]["downstream"]["manifest"]),
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(authority["scope"]["downstream"]["manifest"], manifest),
            "reason": "Add the approved regression test file before implementation continues.",
            "controller_ref": self.metadata["controller_ref"],
            "decision": {"kind": "in-requirement-refinement", "reference": "controller:add-test"}}
        response = progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
            "operation": "scope-revise", "expected_revision": current["revision"], "data": revision})
        self.assertEqual(response["next_action"]["operation"], "stop-host", response)
        stopping = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        late = observer.observation("stopped", "result", {"delivery_id": "late-result",
            "status": "completed", "payload": {"candidate_commit": payload["planning_commit"]}},
            ref="native:dispatcher")
        late["receipt"]["raw"]["resumable"] = True
        late["lifecycle"]["instance"] = "new-fixture-host"
        authorized = progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
            "operation": "observe", "expected_revision": stopping["revision"], "data": late})
        self.assertEqual(authorized["next_action"]["operation"], "stage3-revision-attempt", authorized)
        after = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertEqual(after["scope_recoveries"]["legacy-scope-1"]["original_bytes_base64"],
                         old_recovery["original_bytes_base64"])
        self.assertEqual(after["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                         old_recovery["original_accepted"])
        self.assertEqual(after["scope_recoveries"]["legacy-scope-1"]["bridge"],
                         old_recovery["bridge"])
        transaction = after["scope_revision_transaction"]
        self.assertEqual(transaction["state"], "authorized")
        readiness = progress.revision_scope_readiness(after, self.checkpoint)
        self.assertIsNotNone(readiness)
        revised_scope = copy.deepcopy(after["handoff"]["scope"])
        revised_scope["baseline"] = transaction["snapshot"]["head"]
        revised_scope["downstream"] = copy.deepcopy(readiness["scope_authority"]["scope"]["downstream"])
        revised_scope["implementation_paths"] = copy.deepcopy(readiness["scope_authority"]["scope"]["implementation_paths"])
        revised_scope["closure_paths"] = copy.deepcopy(readiness["scope_authority"]["scope"]["closure_paths"])
        revised_scope["owned_paths"] = copy.deepcopy(revised_scope["implementation_paths"])
        revised_input = {key: copy.deepcopy(after["handoff"][key]) for key in handoff.FIELDS}
        revised_input.update(operation="prepare", scope=revised_scope, readiness=readiness)
        revised_input["authorization"]["reference"] = "controller:add-test"
        revised_input["authorization"]["scope_digest"] = entry.digest(revised_scope)
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            active = progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
                "operation": "activate-scope-revision", "expected_revision": after["revision"],
                "data": {"revision_id": "legacy-add-test-1", "handoff": revised_input}})
        self.assertEqual(active["next_action"]["operation"], "continue-host", active)
        final = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertEqual(final["scope_authority"]["revision"], 2)
        self.assertEqual(final["scope_authority"]["scope"]["implementation_paths"],
                         ["impl.py", "tests/test_impl.py"])
        self.assertEqual(final["scope_revision_transaction"]["state"], "active")
        self.assertEqual(final["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        self.assertEqual([receipt["event"] for receipt in final["dispatch"]["receipts"]],
                         ["existing-dispatcher"])
        self.assertEqual(active["next_action"]["payload"]["ref"], "native:dispatcher")
        self.assertEqual(final["scope_recoveries"]["legacy-scope-1"]["original_bytes_base64"],
                         old_recovery["original_bytes_base64"])
        self.assertEqual(final["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                         old_recovery["original_accepted"])
        self.assertEqual(final["scope_recoveries"]["legacy-scope-1"]["bridge"],
                         old_recovery["bridge"])

    def test_recovered_implementation_handoff_retains_legacy_provenance_in_stage_four(self):
        repository, worktree = self.metadata["repository"], self.metadata["worktree"]
        target_head = subprocess.run(["git", "-C", repository, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        flow_head = subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.addCleanup(lambda: subprocess.run(["git", "-C", repository, "reset", "--hard", target_head],
            capture_output=True, check=True))
        self.addCleanup(lambda: subprocess.run(["git", "-C", worktree, "reset", "--hard", flow_head],
            capture_output=True, check=True))
        inputs, _, record = self.authorized_standalone()
        start = self.recovered_stage_three_start(record, inputs)
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            progress.handle(self.checkpoint, start)
        from test_workflow_progress import ProgressTests
        helper = ProgressTests(methodName="runTest")
        helper.checkpoint = self.checkpoint
        helper.host_response_sequence = 0
        helper.root = Path(self.metadata["repository"])
        helper.flow = Path(self.metadata["worktree"])
        helper.binding = copy.deepcopy(record["binding"])
        helper.frozen = copy.deepcopy(self.original[progress.KEY]["handoff"]["requirement"])
        helper.requirement_path = record["requirement_identity"]["path"]
        helper.request = copy.deepcopy(start["data"]["handoff"]["entry"])
        original_observation = ProgressTests.observation

        def observed(*args, **kwargs):
            value = original_observation(helper, *args, **kwargs)
            if "lifecycle" in value:
                value["lifecycle"]["instance"] = "new-fixture-host"
            return value

        helper.observation = observed
        helper.invoke("observe", helper.observation(ref="native:dispatcher"))
        (helper.flow / "impl.py").write_text("print('recovered')\n")
        helper.flow_git("add", "impl.py")
        helper.flow_git("commit", "-qm", "recovered implementation")
        candidate = helper.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["behavior tested"],
            "candidate_commit": candidate,
            "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"}
                       for axis in ("standards", "spec")},
            "verification": {"candidate": candidate, "checks": ["behavior tested"]}}
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            accepted_view = helper.accept_current(payload, "native:dispatcher")
        self.assertEqual(accepted_view["status"], "accepted", accepted_view)
        stage_three = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertEqual(stage_three["accepted"]["payload"]["candidate_commit"], candidate)
        self.assertEqual(stage_three["scope_recoveries"]["legacy-scope-1"]["state"], "consumed")
        self.assertEqual(stage_three["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                         self.original[progress.KEY]["accepted"])
        self.assertEqual(progress.verify_legacy_recovery(json.loads(self.checkpoint.read_bytes()),
                         self.checkpoint)["state"], "consumed")
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            closure_input = helper.input_for(4, stage_three["accepted"])
            closure_input["authorization"]["flow_mode"] = "continuous"
            started = progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL,
                "operation": "start", "expected_revision": stage_three["revision"],
                "data": {"handoff": closure_input,
                    "native_host": start["data"]["native_host"],
                    "workspace_evidence": record["workspace_evidence"]}})
        self.assertEqual(started["next_action"]["operation"], "invoke-host", started)
        stage_four = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertEqual(stage_four["stage"], 4)
        self.assertEqual(stage_four["scope_recoveries"]["legacy-scope-1"]["original_bytes_base64"],
                         record["original_bytes_base64"])
        self.assertEqual(stage_four["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                         record["original_accepted"])
        self.assertEqual(stage_four["handoff"]["predecessor"], stage_three["accepted"])
        self.assertEqual(progress.verify_legacy_recovery(json.loads(self.checkpoint.read_bytes()),
                         self.checkpoint)["state"], "consumed")
        cwd_patch, settings_patch = self.recovered_host_patches(record)
        with cwd_patch, settings_patch:
            helper.invoke("observe", helper.observation(ref="native:closure"))
            helper.ready_publication(candidate, "controller:closure", "native:closure")
            published = helper.invoke("publication", {"candidate_commit": candidate,
                "expected_target_head": helper.git("rev-parse", "HEAD"),
                "reference": "controller:closure"})
            self.assertEqual(published["next_action"]["operation"], "publication-receipt", published)
            pending = helper.invoke("receive-publication")["pending"]
            completed = helper.invoke("decide", {"decision_id": pending["decision_id"],
                "subject": pending["subject"], "answer": "accept",
                "reference": "controller:final-accept"})
        self.assertEqual(completed["status"], "accepted", completed)
        final = json.loads(self.checkpoint.read_bytes())[progress.KEY]
        self.assertTrue(progress.verify_completion(final)["completed"])
        self.assertEqual(progress.verify_legacy_recovery(json.loads(self.checkpoint.read_bytes()),
                         self.checkpoint)["state"], "consumed")
        self.assertEqual(final["scope_recoveries"]["legacy-scope-1"]["original_bytes_base64"],
                         record["original_bytes_base64"])
        self.assertEqual(final["scope_recoveries"]["legacy-scope-1"]["original_accepted"],
                         record["original_accepted"])
        self.assertTrue(Path(self.metadata["worktree"]).exists())

    def test_inspect_is_read_only(self):
        before = self.checkpoint.read_bytes()
        result = self.recover(self.recovery_input())
        self.assertEqual(result["state"], "inspected", result)
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_inspect_rejects_changed_old_digest_without_writing(self):
        before = self.checkpoint.read_bytes()
        wrong = self.recovery_input()
        wrong["original_checkpoint_sha256"] = "0" * 64
        with self.assertRaises(entry.PreparationError) as raised:
            self.recover(wrong)
        self.assertEqual(raised.exception.code, "legacy_checkpoint_changed")
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_inspect_rejects_any_stage_three_intent_without_writing(self):
        altered = json.loads(self.original_bytes)
        altered["sessions"]["stage3"] = "late-dispatcher"
        self.checkpoint.write_text(json.dumps(altered, sort_keys=True))
        before = self.checkpoint.read_bytes()
        request = self.recovery_input()
        request["original_checkpoint_sha256"] = hashlib.sha256(before).hexdigest()
        with self.assertRaises(entry.PreparationError) as raised:
            self.recover(request)
        self.assertEqual(raised.exception.code, "legacy_recovery_ineligible")
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_inspect_rejects_outer_stage_three_launch_before_any_session(self):
        altered = json.loads(self.original_bytes)
        altered["launch"] = {"stage": "stage3", "state": "prelaunch", "turn": 0}
        self.assertNotIn("stage3", altered["sessions"])
        altered["current_stage"] = "stage2"
        self.checkpoint.write_text(json.dumps(altered, sort_keys=True))
        before = self.checkpoint.read_bytes()
        request = self.recovery_input()
        request["original_checkpoint_sha256"] = hashlib.sha256(before).hexdigest()
        with self.assertRaises(entry.PreparationError) as raised:
            self.recover(request)
        self.assertEqual(raised.exception.code, "legacy_recovery_ineligible")
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_inspect_rejects_unavailable_pinned_old_package(self):
        package = Path(self.metadata["old_pin"]["root"])
        absent = package.with_name(package.name + "-temporarily-absent")
        before = self.checkpoint.read_bytes()
        package.rename(absent)
        try:
            with self.assertRaises(entry.PreparationError) as raised:
                self.recover(self.recovery_input())
            self.assertEqual(raised.exception.code, "package_unavailable")
            self.assertEqual(self.checkpoint.read_bytes(), before)
        finally:
            absent.rename(package)

    def test_copied_checkpoint_cannot_become_a_second_legacy_authority(self):
        copied = self.scratch / "copied-old-record.json"
        copied.write_bytes(self.original_bytes)
        before = copied.read_bytes()
        with self.assertRaises(entry.PreparationError) as raised:
            progress.handle(copied, {"protocol": progress.PROTOCOL,
                "operation": "scope-recover-legacy", "expected_revision": 0,
                "data": self.recovery_input()})
        self.assertEqual(raised.exception.code, "legacy_checkpoint_changed")
        self.assertEqual(copied.read_bytes(), before)


class LegacyNonemptyScopeTests(unittest.TestCase):
    def test_real_old_stage_three_prepared_input_blocks_inspect_without_launch(self):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            OLD_REVISION + "^{commit}"], capture_output=True)
        if available.returncode:
            self.skipTest("historical ba2a1fa runtime is absent from this checkout")
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary).resolve()
            metadata = historical_fixture(OLD_REVISION, scratch, variant="prepared-stage3")
            checkpoint = Path(metadata["checkpoint"])
            before = checkpoint.read_bytes()
            outer = json.loads(before)
            self.assertEqual(outer["stage3_prepared"]["stage"], 3)
            self.assertEqual(outer["current_stage"], "stage2")
            self.assertNotIn("stage3", outer["sessions"])
            with self.assertRaises(entry.PreparationError) as raised:
                progress.handle(checkpoint, {"protocol": progress.PROTOCOL,
                    "operation": "scope-recover-legacy", "expected_revision": 0,
                    "data": {"action": "inspect", "recovery_id": "legacy-prepared-stage3",
                        "original_checkpoint_sha256": metadata["sha256"],
                        "original_accepted_digest": metadata["accepted_digest"],
                        "controller_ref": metadata["controller_ref"],
                        "host_evidence": metadata["host_evidence"]}})
            self.assertEqual(raised.exception.code, "legacy_recovery_ineligible")
            self.assertEqual(checkpoint.read_bytes(), before)

    def test_supplemental_test_file_expands_nonempty_old_implementation_ceiling(self):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            OLD_REVISION + "^{commit}"], capture_output=True)
        if available.returncode:
            self.skipTest("historical ba2a1fa runtime is absent from this checkout")
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary).resolve()
            metadata = historical_fixture(OLD_REVISION, scratch, variant="missing-test")
            scenario = LegacyScopeTests(methodName="runTest")
            scenario.scratch = scratch
            scenario.metadata = metadata
            scenario.checkpoint = Path(metadata["checkpoint"])
            scenario.original_bytes = scenario.checkpoint.read_bytes()
            scenario.original = json.loads(scenario.original_bytes)
            old = scenario.original[progress.KEY]
            self.assertEqual(old["handoff"]["scope"]["implementation_paths"], ["impl.py"])
            self.assertNotIn("tests/test_impl.py", old["handoff"]["scope"]["implementation_paths"])
            inspected = scenario.recover(scenario.recovery_input())
            self.assertEqual(inspected["state"], "inspected")
            self.assertEqual(scenario.checkpoint.read_bytes(), scenario.original_bytes)
            inputs = scenario.takeover_inputs()
            inputs["manifest"].append({"path": "tests/test_impl.py", "owner": "implementation",
                "kind": "test", "operation": "add",
                "basis": {"commit": metadata["planning_commit"], "path": "docs/spec.md",
                          "section": "approved design"}})
            inputs["new_confirmed"]["authority_scope"]["allowed_paths"].append("tests/test_impl.py")
            prepared = scenario.recover(inputs)
            self.assertEqual(prepared["state"], "takeover-pending", prepared)
            activated = scenario.recover({"action": "activate", "recovery_id": "legacy-scope-1",
                "original_checkpoint_sha256": metadata["sha256"],
                "controller_ref": metadata["controller_ref"],
                "host_evidence": metadata["host_evidence"]})
            self.assertEqual(activated["state"], "authorized", activated)
            current = json.loads(scenario.checkpoint.read_bytes())
            recovery = current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
            self.assertEqual(base64.b64decode(recovery["original_bytes_base64"]), scenario.original_bytes)
            self.assertEqual(recovery["original_accepted"], old["accepted"])
            self.assertEqual(recovery["supplemental_authority"]["scope"]["implementation_paths"],
                             ["impl.py", "tests/test_impl.py"])
            self.assertEqual(recovery["bridge"]["protocol"], handoff.PROTOCOL)
            rendered = handoff.render(recovery["bridge"], readiness=activated["readiness"])
            self.assertEqual(rendered["payload"]["allowed_paths"], ["impl.py", "tests/test_impl.py"])


class LegacyPreparationV3Tests(unittest.TestCase):
    def test_original_preparation_v3_is_an_explicit_inspectable_combination(self):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            OLD_PREPARATION_V3_REVISION + "^{commit}"], capture_output=True)
        if available.returncode:
            self.skipTest("historical preparation-v3 runtime is absent from this checkout")
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary).resolve()
            metadata = historical_fixture(OLD_PREPARATION_V3_REVISION, scratch)
            checkpoint = Path(metadata["checkpoint"])
            before = checkpoint.read_bytes()
            self.assertEqual(metadata["old_pin"]["compatibility_key"]["preparation"],
                             "workflow-preparation-v3")
            result = progress.handle(checkpoint, {"protocol": progress.PROTOCOL,
                "operation": "scope-recover-legacy", "expected_revision": 0,
                "data": {"action": "inspect", "recovery_id": "legacy-v3",
                    "original_checkpoint_sha256": metadata["sha256"],
                    "original_accepted_digest": metadata["accepted_digest"],
                    "controller_ref": metadata["controller_ref"],
                    "host_evidence": metadata["host_evidence"]}})
            self.assertEqual(result["state"], "inspected")
            self.assertEqual(checkpoint.read_bytes(), before)


class LegacyCompatibilityBoundaryTests(unittest.TestCase):
    """History-independent guards that also run in shallow CI checkouts."""

    def test_ordinary_current_operations_reject_old_protocol_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "old.json"
            original = {"version": 6, progress.KEY: {"protocol": "workflow-progress-v12",
                "accepted": {"digest": "old-accepted"}, "packages": {
                    "solution-design": {"bundle_digest": "a" * 64}}}}
            record.write_text(json.dumps(original, sort_keys=True))
            before = record.read_bytes()
            with self.assertRaises(entry.PreparationError) as raised:
                progress.handle(record, {"protocol": progress.PROTOCOL,
                    "operation": "inspect", "expected_revision": 0, "data": {}})
            self.assertEqual(raised.exception.code, "legacy_run_requires_original_runtime")
            self.assertEqual(record.read_bytes(), before)

    def test_old_transfer_and_runner_versions_have_no_current_write_path(self):
        with self.assertRaises(entry.PreparationError) as raised:
            handoff.require_current_protocol({"protocol": "workflow-stage-transfer-v7",
                "expected_entry": {"packages": {"solution-design": {"bundle_digest": "a" * 64}}}})
        self.assertEqual(raised.exception.code, "legacy_run_requires_original_runtime")
        script = ROOT / "src/stages/guided-implementation/scripts/workflow.py"
        spec = importlib.util.spec_from_file_location("legacy_boundary_runner", script)
        runner = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(runner)
        with self.assertRaisesRegex(runner.WorkflowError, "legacy_run_requires_original_runtime"):
            runner._validate_record({"version": 6, "confirmed": {"packages": {"runner": {
                "bundle_digest": "b" * 64}}}})


if __name__ == "__main__":
    unittest.main()
