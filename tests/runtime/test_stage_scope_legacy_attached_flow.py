"""An old attached Phase-2 result cannot acquire a missing Topic control slot."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import entry_prepare as entry
import workflow_progress as progress


ROOT = Path(__file__).resolve().parents[2]
OLD_REVISION = "ba2a1fa"


OLD_ATTACHED_FIXTURE = r'''
import hashlib, json, sys, tempfile
from pathlib import Path
source, scratch, pin_path, output = map(Path, sys.argv[1:])
sys.path.insert(0, str(source / 'tests/runtime'))
sys.path.insert(0, str(source / 'src/shared/scripts'))
sys.path.insert(0, str(source / 'src/stages/guided-implementation/scripts'))
class PreservedTemporaryDirectory:
    def __init__(self): self.name = tempfile.mkdtemp(dir=str(scratch))
    def cleanup(self): pass
tempfile.TemporaryDirectory = PreservedTemporaryDirectory
import test_attached_flow_progress as attached
import test_entry_prepare
pin = json.loads(pin_path.read_text())
original_setup = test_entry_prepare.EntrySupport.setUp
def setup_with_old_pin(self):
    original_setup(self)
    self.package.clear()
    self.package.update(pin)
test_entry_prepare.EntrySupport.setUp = setup_with_old_pin
fixture = attached.AttachedFlowTests(methodName='runTest')
fixture.setUp()
fixture.begin('stepwise')
fixture.invoke('observe', fixture.observation())
fixture.activate_designer()
(fixture.flow / 'docs/spec.md').write_text('approved design\n')
fixture.flow_git('add', 'docs/spec.md')
fixture.flow_git('commit', '-qm', 'planning')
candidate = fixture.flow_git('rev-parse', 'HEAD')
fixture.ready_publication(candidate, 'controller:planning')
fixture.invoke('publication', {'candidate_commit':candidate, 'reference':'controller:planning'})
pending = fixture.invoke('receive-publication')['pending']
fixture.invoke('decide', {'decision_id':pending['decision_id'], 'subject':pending['subject'],
    'answer':'accept', 'reference':'controller:accept-plan'})
fixture.complete_phase()
old = fixture.state()
assert old['status'] == 'accepted' and old['phase_complete'] is True
assert old['handoff']['entry']['source']['kind'] == 'discussion'
assert old['control']['discussion'] is None
topic = attached.protocol.handle({**fixture.base, 'operation':'read-topic'})
assert topic['current_phase'] == 2 and topic['workflow_control'] is None
raw = fixture.checkpoint.read_bytes()
output.write_text(json.dumps({'checkpoint':str(fixture.checkpoint.resolve()),
    'sha256':hashlib.sha256(raw).hexdigest(), 'accepted_digest':old['accepted']['digest'],
    'controller_ref':old['handoff']['controller_ref'],
    'host_evidence':{'adapter':'app-server','snapshot':old['lifecycle_snapshot']}}))
'''


class MissingAttachedAuthorityTests(unittest.TestCase):
    def test_old_discussion_result_without_topic_control_is_read_only_rejected(self):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            OLD_REVISION + "^{commit}"], capture_output=True)
        if available.returncode:
            self.skipTest("historical attached runtime is absent from this checkout")
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary).resolve()
            source = scratch / "old-source"
            source.mkdir()
            archived = subprocess.run(["git", "-C", str(ROOT), "archive", OLD_REVISION],
                capture_output=True, check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archived)) as tar:
                tar.extractall(source)
            packages = scratch / "old-packages"
            subprocess.run([sys.executable, str(source / "scripts/build_skills.py"),
                "--output", str(packages), "--package", "solution-design"],
                capture_output=True, check=True)
            package = packages / "solution-design"
            manifest = json.loads((package / "package.json").read_text())
            pin = {"name": "solution-design", "stage": manifest["stage"],
                "entry": str(package / "SKILL.md"), "root": str(package),
                "format_version": manifest["format_version"], "release": manifest["release"],
                "bundle_digest": manifest["bundle_digest"],
                "compatibility_key": manifest["compatibility_key"]}
            pin_path = scratch / "old-pin.json"
            pin_path.write_text(json.dumps(pin))
            output = scratch / "fixture.json"
            built = subprocess.run([sys.executable, "-c", OLD_ATTACHED_FIXTURE,
                str(source), str(scratch), str(pin_path), str(output)],
                cwd=source, capture_output=True, text=True)
            if built.returncode:
                raise AssertionError("old attached fixture failed:\n" + built.stderr[-4000:])
            metadata = json.loads(output.read_text())
            checkpoint = Path(metadata["checkpoint"])
            before = checkpoint.read_bytes()
            old = json.loads(before)[progress.KEY]
            self.assertEqual(old["handoff"]["entry"]["source"]["kind"], "discussion")
            self.assertIsNone(old["control"]["discussion"])
            common = {"recovery_id": "attached-missing-control",
                "original_checkpoint_sha256": metadata["sha256"],
                "original_accepted_digest": metadata["accepted_digest"],
                "controller_ref": metadata["controller_ref"],
                "host_evidence": metadata["host_evidence"]}
            for action in ("inspect", "prepare-takeover"):
                with self.subTest(action=action):
                    with self.assertRaises(entry.PreparationError) as raised:
                        progress.handle(checkpoint, {"protocol": progress.PROTOCOL,
                            "operation": "scope-recover-legacy", "expected_revision": 0,
                            "data": {"action": action, **common}})
                    self.assertEqual(raised.exception.code, "authority_missing")
                    self.assertEqual(checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
