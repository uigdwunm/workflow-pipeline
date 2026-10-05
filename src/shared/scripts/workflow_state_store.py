"""Bounded immutable records committed by the original checkpoint owner.

Opening a root never reads subjects or archives. Callers select explicit subject
IDs, and each operation has a fixed read budget; there is no hydrate operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import entry_prepare as entry

FORMAT = 'workflow-current-v1'
ROOT_LIMIT = SUBJECT_LIMIT = 256 * 1024
EVIDENCE_LIMIT = 16 * 1024 * 1024
READ_LIMIT = 9 * ROOT_LIMIT
RUN_LIMIT = 2 * 1024 * 1024 * 1024
MAX_SUBJECTS = 8
BUSINESS_RESERVE = 4 * EVIDENCE_LIMIT
RECOVERY_RESERVE = 4 * EVIDENCE_LIMIT
_operation_store = ContextVar('workflow_operation_store', default=None)


@contextmanager
def operation_store(checkpoint):
    """Share verified immutable reads only inside one locked owner operation."""
    token = _operation_store.set(StateStore(checkpoint))
    try:
        yield
    finally:
        _operation_store.reset(token)


def open_store(checkpoint, *, receipt_action=None):
    store = _operation_store.get()
    if store is None or store.checkpoint != Path(checkpoint):
        return StateStore(checkpoint, receipt_action=receipt_action)
    if receipt_action is not None:
        store.receipt_action = receipt_action
    return store


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def require(condition, message, code='invalid_state_store'):
    entry.require(condition, code, message)


def atomic_write(path, data):
    path = Path(path)
    require(not path.is_symlink(), 'state path cannot be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass  # replace may have completed before interruption was delivered.


@dataclass(frozen=True)
class CurrentProjection:
    run_id: str
    revision: int
    root: dict
    reference: dict


@dataclass(frozen=True)
class SubjectProjection:
    subject_id: str
    value: dict
    reference: dict


@dataclass(frozen=True)
class IdentityProjection:
    """Read-only current identity; carries no writer lease."""
    record: dict
    files_read: int
    bytes_read: int
    authority_revision: int = 0


class ProjectedRecord(dict):
    """In-memory read stamp; never serialized into a command or write lease."""
    def __init__(self, value, authority_revision=0):
        super().__init__(value)
        self.authority_revision = authority_revision


@dataclass(frozen=True)
class ScopeProjection(IdentityProjection):
    """Current scope and Controller authority, without allocation subjects."""


@dataclass(frozen=True)
class TransactionProjection(IdentityProjection):
    """One specified allocation intent, without a write release."""


@dataclass(frozen=True)
class DeliveryProjection(IdentityProjection):
    """Current delivery facts; archived claims require explicit separate reads."""


class StateStore:
    def __init__(self, checkpoint, *, receipt_action=None):
        self.checkpoint = Path(checkpoint)
        self.directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        self.files_read = 0
        self.bytes_read = 0
        self.receipt_action = receipt_action
        self._verified_objects = {}
        self._run_id = None

    def _charge(self, size):
        path = self._safe('usage.json')
        usage = json.loads(self._read(path, 16 * 1024)) if path.exists() else {'allocated': 0}
        require(type(usage.get('allocated')) is int and usage['allocated'] >= 0,
                'invalid capacity account')
        if usage['allocated'] + size > RUN_LIMIT - BUSINESS_RESERVE - RECOVERY_RESERVE:
            require(self.receipt_action is not None,
                    'run budget exhausted; retain the original pending call', 'state_capacity')
            digest = hashlib.sha256(self.receipt_action.encode()).hexdigest()
            reserves = [self._safe(Path('reserves') / kind / digest)
                        for kind in ('business', 'recovery')]
            reserves = [path for path in reserves if path.exists()]
            require(len(reserves) == 1 and reserves[0].stat().st_size >= size and
                    usage['allocated'] + size <= RUN_LIMIT,
                    'original invocation exhausted its own receipt reserve', 'state_capacity')
            # Transfer real reserved space to this exact original receipt.
            with reserves[0].open('r+b') as stream:
                stream.truncate(reserves[0].stat().st_size - size)
                stream.flush()
                os.fsync(stream.fileno())
        # Charge before allocating: interruption may overcount, never undercount.
        usage['allocated'] += size
        atomic_write(path, encode(usage))

    def reserve(self, action_id, *, recovery=False):
        digest = hashlib.sha256(action_id.encode()).hexdigest()
        directory = self._safe(Path('reserves') / ('recovery' if recovery else 'business'))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / digest
        if path.exists():
            require(not path.is_symlink() and 0 <= path.stat().st_size <= EVIDENCE_LIMIT,
                    'incomplete call reserve', 'state_capacity')
            return digest
        require(len(list(directory.iterdir())) < 4,
                'pending call window is full; query an original invocation', 'state_capacity')
        # Real allocation, rather than a sparse truncate, makes ENOSPC visible
        # before an invoke-host action can escape the durable owner.
        atomic_write(path, bytes(EVIDENCE_LIMIT))
        return digest

    def release_settled_reserves(self, pending):
        retained = {hashlib.sha256(identity.encode()).hexdigest() for identity in pending}
        for category in ('business', 'recovery'):
            directory = self._safe(Path('reserves') / category)
            if not directory.exists():
                continue
            for path in directory.iterdir():
                require(not path.is_symlink(), 'reservation path changed')
                if path.name not in retained:
                    path.unlink()
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _safe(self, relative):
        path = self.directory / relative
        require(path.is_relative_to(self.directory), 'state path escapes its run')
        current = path
        while current != self.directory.parent:
            require(not current.is_symlink(), 'state path cannot traverse symlinks')
            current = current.parent
        return path

    def _read(self, path, maximum):
        require(not path.is_symlink(), 'state path cannot be a symlink')
        try:
            with path.open('rb') as stream:
                data = stream.read(maximum + 1)
        except OSError as error:
            raise entry.PreparationError('state_object_missing', 'required state object is unavailable') from error
        require(len(data) <= maximum, 'state object exceeds its fixed budget', 'state_capacity')
        self.files_read += 1
        self.bytes_read += len(data)
        require(self.bytes_read <= READ_LIMIT + EVIDENCE_LIMIT,
                'operation exceeded its read budget', 'state_capacity')
        return data

    def _path(self, digest):
        require(isinstance(digest, str) and len(digest) == 64 and
                all(c in '0123456789abcdef' for c in digest), 'invalid object digest')
        return self._safe(Path('objects') / digest[:2] / digest[2:4] / digest)

    def write_object(self, run_id, kind, value, *, subject_id=None):
        require(kind in {'root', 'subject', 'scope', 'predecessor', 'native-source-index', 'evidence', 'receipt'}, 'invalid object type')
        body = {'format': FORMAT, 'run_id': run_id, 'kind': kind, 'value': value}
        if subject_id is not None:
            body['subject_id'] = subject_id
        data = encode(body)
        maximum = EVIDENCE_LIMIT if kind == 'evidence' else SUBJECT_LIMIT
        require(len(data) <= maximum, kind + ' exceeds its fixed budget', 'state_capacity')
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if path.exists():
            if digest not in self._verified_objects:
                require(path.stat().st_size == len(data) and self._read(path, maximum) == data,
                        'immutable object was changed', 'state_object_corrupt')
        else:
            self._charge(len(data))
            atomic_write(path, data)
        self._verified_objects[digest] = copy.deepcopy(body)
        return {'run_id': run_id, 'kind': kind, 'sha256': digest, 'size': len(data)}

    def read_evidence(self, reference, *, run_id, kind='evidence'):
        require(isinstance(reference, dict) and set(reference) == {'run_id', 'kind', 'sha256', 'size'} and
                reference['run_id'] == run_id and reference['kind'] == kind and
                type(reference['size']) is int, 'cross-run or malformed object reference')
        limit = EVIDENCE_LIMIT if kind == 'evidence' else SUBJECT_LIMIT
        cached = self._verified_objects.get(reference['sha256'])
        if cached is not None:
            require(cached.get('run_id') == run_id and cached.get('kind') == kind and
                    len(encode(cached)) == reference['size'], 'cached reference identity changed')
            return copy.deepcopy(cached)
        data = self._read(self._path(reference['sha256']), limit)
        require(len(data) == reference['size'] and hashlib.sha256(data).hexdigest() == reference['sha256'],
                'object digest or size changed', 'state_object_corrupt')
        try:
            body = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise entry.PreparationError('state_object_corrupt', 'state object is not valid JSON') from error
        require(body.get('format') == FORMAT and body.get('run_id') == run_id and body.get('kind') == kind,
                'object identity changed', 'state_object_corrupt')
        self._verified_objects[reference['sha256']] = copy.deepcopy(body)
        return body

    def open_current(self):
        if not self.checkpoint.exists():
            return None
        envelope = json.loads(self._read(self.checkpoint, ROOT_LIMIT))
        require(envelope.get('store_format') == FORMAT, 'retain original checkpoint runtime',
                'legacy_run_requires_original_runtime')
        root = self.read_evidence(envelope['current'], run_id=envelope['run_id'], kind='root')['value']
        require(root.get('revision') == envelope['revision'] and
                isinstance(root.get('subjects'), dict) and len(root['subjects']) + ('scope' in root) <= MAX_SUBJECTS,
                'invalid current root')
        self._run_id = envelope['run_id']
        return CurrentProjection(envelope['run_id'], envelope['revision'], root, envelope['current'])

    def read_subject(self, current, subject_id):
        require(isinstance(subject_id, str) and subject_id and subject_id != 'all', 'exact subject ID required')
        reference = current.root['subjects'].get(subject_id)
        require(reference is not None, 'subject is not in the current window', 'subject_missing')
        body = self.read_evidence(reference, run_id=current.run_id, kind='subject')
        require(body.get('subject_id') == subject_id and isinstance(body['value'], dict), 'subject identity changed')
        require(self.bytes_read <= READ_LIMIT, 'current subjects exceed the operation budget', 'state_capacity')
        return SubjectProjection(subject_id, body['value'], reference)

    def write_allocation_part(self, current, part, value):
        require(part in {'contract', 'package', 'baseline'} and isinstance(value, dict),
                'invalid allocation contract part')
        return self.write_object(current.run_id, 'evidence', {'allocation_part': part, 'value': value})

    def read_allocation_part(self, current, part, reference):
        value = self.read_evidence(reference, run_id=current.run_id)['value']
        require(isinstance(value, dict) and set(value) == {'allocation_part', 'value'} and
                value['allocation_part'] == part and isinstance(value['value'], dict),
                'allocation contract part changed', 'state_object_corrupt')
        return value['value']

    def write_predecessor(self, current, value):
        """Persist only the verified design facts, never an old current root."""
        require(isinstance(value, dict) and value.get('kind') == 'accepted-design-v1',
                'invalid predecessor facts')
        return self.write_object(current.run_id, 'predecessor', value)

    def read_predecessor(self, current, reference):
        value = self.read_evidence(reference, run_id=current.run_id, kind='predecessor')['value']
        require(isinstance(value, dict) and value.get('kind') == 'accepted-design-v1' and
                value.get('checkpoint_path') == str(self.checkpoint.resolve()),
                'predecessor belongs to another Controller checkpoint')
        require(self.bytes_read <= READ_LIMIT, 'predecessor exceeds read budget', 'state_capacity')
        return value

    def read_scope(self, current, subject_id):
        """Read the one current Stage-2 scope, in place of its full subject."""
        require(subject_id == current.root['metadata'].get('stage_subject'), 'scope subject changed')
        reference = current.root.get('scope')
        require(reference is not None, 'current scope projection is missing', 'state_object_missing')
        body = self.read_evidence(reference, run_id=current.run_id, kind='scope')['value']
        require(isinstance(body, dict) and set(body) == {'subject_id', 'subject_ref', 'value'} and
                body['subject_id'] == subject_id and
                body['subject_ref'] == current.root['subjects'].get(subject_id) and
                isinstance(body['value'], dict), 'scope does not name the current stage subject')
        require(self.bytes_read <= READ_LIMIT, 'scope exceeds the current read budget', 'state_capacity')
        return body['value']

    def _poll_index_path(self, kind, key):
        require(kind in {'event', 'response', 'call'}, 'invalid poll fingerprint kind')
        self._path(key)  # Validate the digest, never use external IDs as paths.
        return self._safe(Path('poll-fingerprints') / kind / key[:2] / key[2:4] / key)

    def poll_fingerprint(self, kind, key):
        """Direct conflict lookup; this is never an applied-operation receipt."""
        if self._run_id is None:
            self.open_current()
        require(self._run_id is not None, 'open the current authority before reading fingerprints')
        path = self._poll_index_path(kind, key)
        if not path.exists():
            return None
        record = json.loads(self._read(path, 512))
        require(isinstance(record, dict) and set(record) == {'run_id', 'digest'} and
                record['run_id'] == self._run_id and isinstance(record['digest'], str) and
                len(record['digest']) == 64 and all(c in '0123456789abcdef' for c in record['digest']),
                'invalid poll conflict fingerprint', 'state_object_corrupt')
        return record['digest']

    def record_poll(self, *, revision, identity, first, latest, fingerprints):
        """Telemetry does not create a new authority revision or evidence object."""
        envelope = json.loads(self._read(self.checkpoint, ROOT_LIMIT))
        require(envelope.get('store_format') == FORMAT and envelope.get('revision') == revision and
                envelope.get('run_id') == self._run_id,
                'checkpoint revision changed', 'stale_checkpoint')
        previous = envelope.get('poll', {})
        require(len(encode(first)) <= 16 * 1024 and len(encode(latest)) <= 16 * 1024,
                'poll attribution exceeds its fixed budget', 'state_capacity')
        envelope['poll'] = {'identity': identity, 'count': min(2**63 - 1,
            previous.get('count', 0) + 1 if previous.get('identity') == identity else 1),
            'last_seen': time.time_ns(),
            'first': previous.get('first', first) if previous.get('identity') == identity else first,
            'latest': latest}
        data = encode(envelope)
        require(len(data) <= ROOT_LIMIT, 'current root exceeds its fixed budget', 'state_capacity')
        additions = []
        for kind, key, digest in fingerprints:
            known = self.poll_fingerprint(kind, key)
            require(known is None or known == digest, 'poll fingerprint conflict', 'state_object_corrupt')
            if known is None:
                additions.append((self._poll_index_path(kind, key), encode({'run_id': self._run_id, 'digest': digest})))
        if additions:
            # Charge file allocation units and new directories, not just the
            # small JSON payload. The run's existing account remains conservative.
            block = os.statvfs(self.directory).f_frsize or 4096
            directories = {parent for path, _ in additions for parent in path.parents
                           if parent != self.directory and parent.is_relative_to(self.directory) and not parent.exists()}
            self._charge(sum(((len(body) + block - 1) // block) * block for _, body in additions) +
                         len(directories) * block)
            for path, body in additions:
                atomic_write(path, body)
        # Fingerprints written before this point only reject conflicts; they do
        # not prove a business mutation or advance its authority revision.
        atomic_write(self.checkpoint, data)

    def _ensure_marker(self, current):
        marker = self._safe(Path('commits') / str(current.revision))
        proof = encode({'revision': current.revision, 'root': current.reference['sha256'],
                        'receipts': current.root.get('receipts', [])})
        if marker.exists():
            require(self._read(marker, 16 * 1024) == proof,
                    'commit proof differs from the current authority', 'state_object_corrupt')
        else:
            atomic_write(marker, proof)

    def receipt_key(self, current, *, actor, kind, subject, invocation):
        require(all(isinstance(value, str) and value and len(value.encode()) <= 4096
                    for value in (actor, kind, subject, invocation)), 'invalid invocation identity')
        return hashlib.sha256(encode([current.run_id, actor, kind, subject, invocation])).hexdigest()

    def _receipt_path(self, key):
        self._path(key)  # Validate the bounded hash before using it as a path.
        return self._safe(Path('receipts') / key[:2] / key[2:4] / key)

    def prepare_receipt(self, current, key, request, result):
        return {'key': key, 'request_digest': hashlib.sha256(encode(request)).hexdigest(),
            'request': self.write_object(current.run_id, 'evidence', request),
            'result': self.write_object(current.run_id, 'evidence', result)}

    def read_receipt(self, current, key, request):
        path = self._receipt_path(key)
        if not path.exists():
            return None
        receipt = json.loads(self._read(path, 16 * 1024))
        require(receipt.get('run_id') == current.run_id and receipt.get('key') == key and
                receipt.get('request_digest') == hashlib.sha256(encode(request)).hexdigest(),
                'invocation key was reused with different input', 'receipt_conflict')
        revision = receipt.get('revision')
        require(type(revision) is int and revision > 0, 'invalid receipt revision')
        marker = self._safe(Path('commits') / str(revision))
        if not marker.exists():
            if revision != current.revision or receipt.get('root') != current.reference['sha256']:
                return None  # Prepared, but not proven committed.
            self._ensure_marker(current)
        proof = json.loads(self._read(marker, 16 * 1024))
        membership = {name: receipt[name] for name in ('key', 'request_digest', 'request', 'result')}
        require(proof.get('revision') == revision and proof.get('root') == receipt.get('root') and
                membership in proof.get('receipts', []),
                'receipt lacks an authority commit proof', 'state_object_corrupt')
        return self.read_evidence(receipt['result'], run_id=current.run_id)['value']

    def commit_current(self, *, expected_revision, revision, subjects, metadata, outer, receipts=(), initial_outer=None,
                       scope=None):
        if initial_outer is not None:
            require(self.checkpoint.exists() and not self.directory.exists() and
                    json.loads(self._read(self.checkpoint, ROOT_LIMIT)) == initial_outer and
                    'store_format' not in initial_outer and 'workflow_progress' not in initial_outer and
                    initial_outer == outer,
                    'initial preparation envelope changed', 'stale_checkpoint')
            current = None
        else:
            current = self.open_current() if self.checkpoint.exists() else None
        require(current is not None or not self.directory.exists(),
                'original checkpoint pointer is missing; retain its state directory and recover the original owner',
                'state_authority_missing')
        actual = current.revision if current else 0
        require(actual == expected_revision, 'checkpoint revision changed', 'stale_checkpoint')
        if current is not None:
            self._ensure_marker(current)
        require(type(revision) is int and revision > actual and len(subjects) + (scope is not None) <= MAX_SUBJECTS,
                'invalid revision or active subject count', 'state_capacity')
        run_id = current.run_id if current else str(uuid.uuid4())
        # Validate the complete bounded candidate before writing any object.
        for name, value in subjects.items():
            require(isinstance(name, str) and name and isinstance(value, dict) and
                    len(encode({'format': FORMAT, 'run_id': run_id, 'kind': 'subject',
                                'subject_id': name, 'value': value})) <= SUBJECT_LIMIT,
                    'subject exceeds its fixed budget', 'state_capacity')
        if scope is not None:
            subject_id = metadata['stage_subject']
            require(subject_id in subjects and isinstance(scope, dict), 'scope requires its current subject')
            subject_bytes = encode({'format': FORMAT, 'run_id': run_id, 'kind': 'subject',
                                    'subject_id': subject_id, 'value': subjects[subject_id]})
            scope_body = {'subject_id': subject_id, 'subject_ref': {
                'run_id': run_id, 'kind': 'subject', 'sha256': hashlib.sha256(subject_bytes).hexdigest(),
                'size': len(subject_bytes)}, 'value': scope}
            require(len(encode({'format': FORMAT, 'run_id': run_id, 'kind': 'scope',
                                'value': scope_body})) <= SUBJECT_LIMIT,
                    'scope exceeds its fixed budget', 'state_capacity')
        references = {name: self.write_object(run_id, 'subject', value, subject_id=name)
                      for name, value in subjects.items()}
        require(len(receipts) <= 16, 'receipt commit batch exceeds its fixed proof budget', 'state_capacity')
        root = {'revision': revision, 'subjects': references, 'metadata': metadata, 'outer': outer,
                'receipts': list(receipts)}
        if scope is not None:
            root['scope'] = self.write_object(run_id, 'scope', scope_body)
        return self._commit_root(current, run_id, root)

    def commit_outer(self, *, expected_revision, outer):
        """Runner owns only outer fields; retain exact Controller subject refs."""
        current = self.open_current()
        require(current is not None and current.revision == expected_revision,
                'checkpoint revision changed', 'stale_checkpoint')
        self._ensure_marker(current)
        if outer == current.root['outer']:
            return current
        root = {**current.root, 'revision': current.revision + 1,
                'outer': outer, 'receipts': []}
        return self._commit_root(current, current.run_id, root)

    def _commit_root(self, current, run_id, root):
        revision, receipts, metadata = root['revision'], root['receipts'], root['metadata']
        reference = self.write_object(run_id, 'root', root)
        for receipt in receipts:
            index = {**receipt, 'revision': revision, 'run_id': run_id, 'root': reference['sha256']}
            require(len(encode(index)) <= 16 * 1024, 'receipt index exceeds its fixed budget', 'state_capacity')
            atomic_write(self._receipt_path(receipt['key']), encode(index))
        envelope = {'store_format': FORMAT, 'run_id': run_id, 'revision': revision,
                    'current': reference, 'previous': current.reference if current else None,
                    'workflow_progress': metadata}
        data = encode(envelope)
        require(len(data) <= ROOT_LIMIT, 'current root exceeds its fixed budget', 'state_capacity')
        atomic_write(self.checkpoint, data)
        committed = CurrentProjection(run_id, revision, root, reference)
        self._ensure_marker(committed)
        return committed
