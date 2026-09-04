"""Dependency-free verification of published checkpoint artifacts."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any, Callable


def current_checkpoint_artifact(
    checkpoint: dict[str, Any], *, topic_path: Path,
    sha256: Callable[[bytes], str], canonical_json: Callable[[Any], str],
    read_regular: Callable[[Path, str], bytes],
) -> dict[str, Any] | None:
    """Return normalized published authority only when its artifact is current."""
    try:
        storage_kind = checkpoint.get("storage_kind")
        if storage_kind == "git":
            project = Path(subprocess.run(
                ["git", "-C", str(topic_path.parent), "rev-parse", "--show-toplevel"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            ).stdout.strip())
            def git(*args: str) -> bytes:
                return subprocess.run(["git", "-C", str(project), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
            identity, ref = checkpoint["published_identity"], checkpoint["checkpoint_ref"]
            if not isinstance(identity, str) or not isinstance(ref, str) or git("rev-parse", "--verify", ref).decode("ascii").strip() != identity or git("cat-file", "-t", identity).decode("ascii").strip() != "commit":
                return None
            header, message = git("cat-file", "-p", identity).decode("utf-8").split("\n\n", 1)
            if [line.split(" ", 1)[1] for line in header.splitlines() if line.startswith("parent ")] != [checkpoint.get("replacement_parent") or checkpoint["base_commit"]]:
                return None
            trailers = {key: value for key, value in (line.split(": ", 1) for line in message.splitlines() if ": " in line and line.startswith("Codex-"))}
            paths, blobs, digests = json.loads(checkpoint["paths_json"]), json.loads(checkpoint["blob_ids_json"]), json.loads(checkpoint["document_digests_json"])
            expected = {"Codex-Discussion-Checkpoint": checkpoint["checkpoint_id"], "Codex-Document-SHA256": digests[paths[0]], "Codex-Discussion-Decision-SHA256": checkpoint["decision_digest"], "Codex-Discussion-Paths-SHA256": checkpoint["path_set_digest"]}
            if trailers != expected or sorted(git("diff-tree", "--no-commit-id", "--name-only", "-r", identity).decode("utf-8").splitlines()) != paths:
                return None
            if any(git("rev-parse", f"{identity}:{path}").decode("ascii").strip() != blobs[path] or sha256(git("cat-file", "blob", blobs[path])) != digests[path] for path in paths):
                return None
            document_digests = digests
        elif storage_kind == "non-git":
            snapshot_bytes = read_regular(Path(checkpoint["snapshot_path"]), "checkpoint snapshot")
            if sha256(snapshot_bytes) != checkpoint["published_identity"] or base64.b64decode(checkpoint["snapshot_bytes_b64"], validate=True) != snapshot_bytes:
                return None
            snapshot = json.loads(snapshot_bytes.decode("utf-8"))
            if not isinstance(snapshot, dict) or (canonical_json(snapshot) + "\n").encode("utf-8") != snapshot_bytes or snapshot.get("purpose") != "stage-entry" or snapshot.get("decision_digest") != checkpoint.get("decision_digest") or snapshot.get("document_digests") != json.loads(checkpoint["document_digests_json"]) or snapshot.get("paths") != json.loads(checkpoint["paths_json"]) or snapshot.get("path_set_digest") != checkpoint.get("path_set_digest"):
                return None
            documents = {path: base64.b64decode(value, validate=True) for path, value in snapshot.get("documents", {}).items() if isinstance(path, str) and isinstance(value, str)}
            if set(documents) != set(snapshot["paths"]) or {path: sha256(value) for path, value in documents.items()} != snapshot["document_digests"]:
                return None
            document_digests = snapshot["document_digests"]
        else:
            return None
        if sha256(read_regular(topic_path, "topic document")) not in document_digests.values():
            return None
        return {"published_identity": checkpoint["published_identity"], "document_digests": document_digests}
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, subprocess.SubprocessError):
        return None
