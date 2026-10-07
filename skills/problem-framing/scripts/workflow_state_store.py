"""Bounded immutable records committed by the original checkpoint owner.

Opening a root never reads subjects or archives. Callers select explicit subject
IDs, and each operation has a fixed read budget; there is no hydrate operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import errno
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
_operation_read_totals = ContextVar('workflow_operation_read_totals', default=None)
_measurement_stores = ContextVar('workflow_measurement_stores', default={})


@contextmanager
def measure_reads():
    """Share telemetry without extending a mutation Store's owner lifetime."""
    totals = _operation_read_totals.get()
    if totals is None:
        totals = {'files': 0, 'bytes': 0, 'coverage': 'state-store-only'}
    token = _operation_read_totals.set(totals)
    try:
        yield totals
    finally:
        _operation_read_totals.reset(token)


@contextmanager
def operation_store(checkpoint):
    """Share verified immutable reads only inside one locked owner operation."""
    existing = _operation_store.get()
    if existing is not None and existing.checkpoint == Path(checkpoint):
        yield existing
        return
    with measure_reads():
        store = StateStore(checkpoint)
        token = _operation_store.set(store)
        try:
            yield store
        finally:
            _operation_store.reset(token)
        if store._pending_reserves is not None:
            store.release_settled_reserves(store._pending_reserves)


def open_store(checkpoint, *, receipt_action=None):
    store = _operation_store.get()
    if store is None or store.checkpoint != Path(checkpoint):
        measured = _measurement_stores.get().get(str(Path(checkpoint).resolve()))
        if measured is None:
            return StateStore(checkpoint, receipt_action=receipt_action)
        store = measured
    if receipt_action is not None:
        store.receipt_action = receipt_action
    return store


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def raw_json_parts(value, ancestors=None):
    """Canonical JSON without whole-string encoding; input objects already exist.

    Sorting an object retains a list of its key references. This bounds string
    encoding buffers, not the host's object construction or key-list memory.
    """
    if isinstance(value, str):
        yield b'"'
        for start in range(0, len(value), 4096):
            yield json.dumps(value[start:start + 4096], ensure_ascii=False)[1:-1].encode('utf-8')
        yield b'"'
    elif isinstance(value, (dict, list)):
        if ancestors is None:
            ancestors = set()
        identity = id(value)
        require(identity not in ancestors, 'raw evidence contains a circular reference')
        ancestors.add(identity)
        try:
            if isinstance(value, dict):
                require(all(isinstance(key, str) for key in value), 'raw evidence requires string-keyed JSON objects')
                yield b'{'
                for number, key in enumerate(sorted(value)):
                    if number:
                        yield b','
                    yield from raw_json_parts(key, ancestors)
                    yield b':'
                    yield from raw_json_parts(value[key], ancestors)
                yield b'}'
            else:
                yield b'['
                for number, item in enumerate(value):
                    if number:
                        yield b','
                    yield from raw_json_parts(item, ancestors)
                yield b']'
        finally:
            ancestors.remove(identity)
    else:
        require(value is None or type(value) in {bool, int, float}, 'raw evidence must be JSON data')
        yield encode(value)


def raw_measure(value):
    size, digest = 0, hashlib.sha256()
    try:
        for part in raw_json_parts(value):
            size += len(part)
            digest.update(part)
    except (RecursionError, ValueError, UnicodeError) as error:
        raise entry.PreparationError('invalid_state_store', 'raw evidence cannot be encoded as JSON') from error
    return size, digest.hexdigest()


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


class RawEvidenceInterrupted(KeyboardInterrupt):
    """A retained partial manifest; callers must save its reference before stopping."""
    def __init__(self, reference):
        super().__init__('raw evidence reception interrupted')
        self.reference = reference


class StateStore:
    def __init__(self, checkpoint, *, receipt_action=None):
        self.checkpoint = Path(checkpoint)
        self.directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        self.files_read = 0
        self.bytes_read = 0
        self._read_totals = _operation_read_totals.get()
        if self._read_totals is None:
            self._read_totals = {'files': 0, 'bytes': 0, 'coverage': 'state-store-only'}
        self.receipt_action = receipt_action
        self._verified_objects = {}
        self._verified_raw_chunks = {}
        self._run_id = None
        self._pending_reserves = None
        self._measured_writes = None
        self._measured_current = None
        self._measure_sequence = False
        self._review_measurement = None

    @contextmanager
    def measure_writes(self, *, sequence=False, ledger_review=None):
        """Encode a bounded candidate through the normal commit path without publishing it."""
        require(_operation_store.get() is self and self._measured_writes is None,
                'native commit measurement requires its locked owner operation')
        cached, pending = self._verified_objects.copy(), self._pending_reserves
        original_action, original_run = self.receipt_action, self._run_id
        measured = {}
        ledger, ledger_saved = None, None
        stores = {str(self.checkpoint.resolve()): self}
        if ledger_review is not None:
            request = copy.deepcopy(ledger_review)
            current = self.open_current()
            stage = self.read_subject(current, current.root['metadata']['stage_subject']).value
            pointer = stage.get('control', {}).get('store_ref')
            require(current.root['metadata']['stage'] == 3 and isinstance(pointer, dict) and
                    request.get('operation') == 'workflow-control' and request.get('action') == 'review-start' and
                    request.get('expected_control_ref') == pointer and
                    request.get('actor_conversation_ref') == current.root['metadata']['controller_ref'] and
                    request.get('actor_topic_id') == pointer['topic_id'] and
                    isinstance(request.get('evidence'), dict) and set(request['evidence']) == {'candidate'},
                    'measurement must name the original Stage-3 review-start and ledger pointer')
            ledger = StateStore(pointer['ledger_path'])
            context, _ = ledger.read_ledger_control(pointer)
            require(context['stage'] == 3 and context['handoff_progress']['candidate'] == request['evidence']['candidate'] and
                    context['handoff_progress']['binding'] == stage['handoff']['binding'] ==
                        current.root['metadata']['binding'] and stage['packages'] == current.root['metadata']['packages'],
                    'measurement changed the original review candidate, package or binding')
            ledger_saved = (ledger._verified_objects.copy(), ledger.receipt_action, ledger._run_id)
            ledger._measured_writes = measured
            ledger._review_measurement = {'request': request, 'pointer': pointer,
                                         'binding': copy.deepcopy(stage['handoff']['binding'])}
            stores[str(ledger.checkpoint.resolve())] = ledger
        token = _measurement_stores.set(stores)
        self._measured_writes = measured
        self._measure_sequence = sequence
        try:
            yield measured
        finally:
            _measurement_stores.reset(token)
            if ledger is not None:
                ledger._verified_objects, ledger.receipt_action, ledger._run_id = ledger_saved
                ledger._measured_writes = None
                ledger._review_measurement = None
            self._verified_objects = cached
            self._pending_reserves = pending
            self._measured_writes = None
            self._measured_current = None
            self._measure_sequence = False
            self.receipt_action, self._run_id = original_action, original_run

    def _charge(self, size, *, allow_reserve=True):
        require(self._measured_writes is None, 'commit measurement cannot charge capacity')
        path = self._safe('usage.json')
        usage = json.loads(self._read(path, 16 * 1024)) if path.exists() else {'allocated': 0}
        require(type(usage.get('allocated')) is int and usage['allocated'] >= 0,
                'invalid capacity account')
        if usage['allocated'] + size > RUN_LIMIT - BUSINESS_RESERVE - RECOVERY_RESERVE:
            require(allow_reserve and self.receipt_action is not None,
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

    def require_ordinary_capacity(self, size):
        """Check a measured new intent before creating its physical call reserve."""
        require(type(size) is int and size > 0, 'invalid intent capacity estimate')
        path = self._safe('usage.json')
        usage = json.loads(self._read(path, 16 * 1024)) if path.exists() else {'allocated': 0}
        require(type(usage.get('allocated')) is int and usage['allocated'] >= 0, 'invalid capacity account')
        require(usage['allocated'] + size <= RUN_LIMIT - BUSINESS_RESERVE - RECOVERY_RESERVE,
                'new intent exceeds ordinary capacity; retain existing invocations', 'state_capacity')

    def reserve(self, action_id, *, recovery=False):
        require(self._measured_writes is None, 'commit measurement cannot reserve capacity')
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
        if self._measured_writes is not None:
            return
        if _operation_store.get() is self:
            # One receipt may require several authority commits. Keep its
            # reservation until the entire locked operation has completed.
            self._pending_reserves = set(pending)
            return
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

    def require_reserve(self, action_id, *, recovery=False):
        digest = hashlib.sha256(action_id.encode()).hexdigest()
        path = self._safe(Path('reserves') / ('recovery' if recovery else 'business') / digest)
        require(path.is_file() and 0 < path.stat().st_size <= EVIDENCE_LIMIT,
                'original invocation has no usable receipt reserve; retain its identity', 'state_capacity')

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
        totals = _operation_read_totals.get()
        if totals is None:
            totals = self._read_totals
        totals['files'] += 1
        totals['bytes'] += len(data)
        require(totals['bytes'] <= READ_LIMIT + EVIDENCE_LIMIT,
                'combined operation exceeded its read budget', 'state_capacity')
        require(self.bytes_read <= READ_LIMIT + EVIDENCE_LIMIT,
                'operation exceeded its read budget', 'state_capacity')
        return data

    @property
    def read_totals(self):
        """Actual physical Store reads, including dependency/ledger stores."""
        totals = _operation_read_totals.get()
        return dict(self._read_totals if totals is None else totals)

    def _path(self, digest):
        require(isinstance(digest, str) and len(digest) == 64 and
                all(c in '0123456789abcdef' for c in digest), 'invalid object digest')
        return self._safe(Path('objects') / digest[:2] / digest[2:4] / digest)

    def write_object(self, run_id, kind, value, *, subject_id=None):
        require(kind in {'root', 'subject', 'scope', 'predecessor', 'native-source-index', 'evidence', 'receipt',
                         'ledger-control', 'ledger-handoff'}, 'invalid object type')
        body = {'format': FORMAT, 'run_id': run_id, 'kind': kind, 'value': value}
        if subject_id is not None:
            body['subject_id'] = subject_id
        data = encode(body)
        maximum = EVIDENCE_LIMIT if kind == 'evidence' else SUBJECT_LIMIT
        require(len(data) <= maximum, kind + ' exceeds its fixed budget', 'state_capacity')
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if self._measured_writes is not None:
            if not path.exists():
                self._measured_writes[str(path)] = len(data)
        elif path.exists():
            if digest not in self._verified_objects:
                require(path.stat().st_size == len(data) and self._read(path, maximum) == data,
                        'immutable object was changed', 'state_object_corrupt')
        else:
            self._charge(len(data))
            atomic_write(path, data)
        self._verified_objects[digest] = copy.deepcopy(body)
        return {'run_id': run_id, 'kind': kind, 'sha256': digest, 'size': len(data)}

    def write_raw_evidence(self, run_id, value, *, measurement, commit_bytes, native_receipt):
        """Spool chunks, retaining bounded commit space in this invocation's reserve."""
        chunks, pending, digest, size, retained = [], bytearray(), hashlib.sha256(), 0, 0
        complete, reason, interruption = True, None, None
        expected_size, expected_digest = measurement
        def flush():
            nonlocal retained
            data = bytes(pending)
            chunk_digest = hashlib.sha256(data).hexdigest()
            reference = {'run_id': run_id, 'kind': 'raw-chunk', 'sha256': chunk_digest, 'size': len(data)}
            require(len(encode(chunks + [reference])) <= SUBJECT_LIMIT - 4096,
                    'raw evidence manifest exceeds its fixed budget', 'state_capacity')
            path = self._path(chunk_digest)
            if path.exists():
                if chunk_digest not in self._verified_raw_chunks:
                    require(path.stat().st_size == len(data) and self._read(path, 1024 * 1024) == data,
                            'immutable raw chunk changed', 'state_object_corrupt')
            else:
                usage_path = self._safe('usage.json')
                usage = json.loads(self._read(usage_path, 16 * 1024)) if usage_path.exists() else {'allocated': 0}
                require(type(usage.get('allocated')) is int and usage['allocated'] >= 0, 'invalid capacity account')
                ordinary = max(0, RUN_LIMIT - BUSINESS_RESERVE - RECOVERY_RESERVE - usage['allocated'])
                from_reserve = max(0, len(data) - ordinary)
                if from_reserve:
                    require(expected_size <= EVIDENCE_LIMIT and self.receipt_action is not None,
                            'raw evidence exhausted ordinary capacity', 'state_capacity')
                    identity = hashlib.sha256(self.receipt_action.encode()).hexdigest()
                    reservations = [self._safe(Path('reserves') / kind / identity)
                                    for kind in ('business', 'recovery')]
                    reservations = [item for item in reservations if item.exists()]
                    require(len(reservations) == 1 and reservations[0].stat().st_size >= from_reserve + commit_bytes,
                            'original receipt must retain its measured commit space', 'state_capacity')
                # Consume the actual remaining ordinary bytes before transferring
                # this call's reserve. No other invocation can pay for this raw.
                ordinary_charge = min(len(data), ordinary)
                if ordinary_charge:
                    self._charge(ordinary_charge, allow_reserve=False)
                if from_reserve:
                    self._charge(from_reserve)
                atomic_write(path, data)
            self._verified_raw_chunks[chunk_digest] = len(data)
            chunks.append(reference)
            retained += len(data)
            pending.clear()
        for part in raw_json_parts(value):
            digest.update(part)
            size += len(part)
            if not complete:
                continue
            while part:
                take = min(1024 * 1024 - len(pending), len(part))
                pending.extend(part[:take])
                part = part[take:]
                if len(pending) == 1024 * 1024:
                    try:
                        flush()
                    except entry.PreparationError as error:
                        if error.code != 'state_capacity':
                            raise
                        complete, reason = False, 'state_capacity'
                        pending.clear()
                        break
                    except OSError as error:
                        if error.errno not in {errno.ENOSPC, errno.EDQUOT}:
                            raise
                        complete, reason = False, 'storage_full'
                        pending.clear()
                        break
                    except KeyboardInterrupt as error:
                        complete, reason, interruption = False, 'interrupted', error
                        pending.clear()
                        break
            if interruption is not None:
                break
        if complete and pending:
            try:
                flush()
            except entry.PreparationError as error:
                if error.code != 'state_capacity':
                    raise
                complete, reason = False, 'state_capacity'
            except OSError as error:
                if error.errno not in {errno.ENOSPC, errno.EDQUOT}:
                    raise
                complete, reason = False, 'storage_full'
            except KeyboardInterrupt as error:
                complete, reason, interruption = False, 'interrupted', error
        if interruption is None:
            require((size, digest.hexdigest()) == measurement, 'raw evidence changed during reception')
        manifest = {'format': 'raw-evidence-v1', 'complete': complete, 'size': expected_size,
                    'retained_size': retained, 'sha256': expected_digest, 'chunks': chunks,
                    'reason': reason, 'native_receipt': native_receipt}
        require(len(encode(manifest)) <= SUBJECT_LIMIT - 1024,
                'raw evidence manifest exceeds its fixed budget', 'state_capacity')
        reference = self.write_object(run_id, 'evidence', manifest)
        if interruption is not None:
            raise RawEvidenceInterrupted(reference) from interruption
        return reference, manifest

    def read_raw_manifest(self, reference, *, run_id):
        """Verify the finite manifest without reading any referenced raw chunks."""
        require(isinstance(reference, dict) and type(reference.get('size')) is int and
                0 < reference['size'] <= SUBJECT_LIMIT and isinstance(reference.get('sha256'), str) and
                len(reference['sha256']) == 64 and all(char in '0123456789abcdef' for char in reference['sha256']),
                'invalid raw manifest reference')
        manifest = self.read_evidence(reference, run_id=run_id, maximum=SUBJECT_LIMIT)['value']
        require(isinstance(manifest, dict) and manifest.get('format') == 'raw-evidence-v1' and
                type(manifest.get('complete')) is bool and type(manifest.get('size')) is int and
                manifest['size'] > 0 and type(manifest.get('retained_size')) is int and
                0 <= manifest['retained_size'] <= manifest['size'] and
                isinstance(manifest.get('sha256'), str) and len(manifest['sha256']) == 64 and
                all(char in '0123456789abcdef' for char in manifest['sha256']) and
                isinstance(manifest.get('chunks'), list), 'invalid raw manifest')
        retained = 0
        for chunk in manifest['chunks']:
            require(isinstance(chunk, dict) and set(chunk) == {'run_id', 'kind', 'sha256', 'size'} and
                    chunk['run_id'] == run_id and chunk['kind'] == 'raw-chunk' and
                    type(chunk['size']) is int and 0 < chunk['size'] <= 1024 * 1024 and
                    isinstance(chunk['sha256'], str) and len(chunk['sha256']) == 64 and
                    all(char in '0123456789abcdef' for char in chunk['sha256']),
                    'invalid raw manifest chunk reference')
            retained += chunk['size']
        require(retained == manifest['retained_size'] and
                (manifest['complete'] and retained == manifest['size'] and manifest.get('reason') is None or
                 not manifest['complete'] and manifest.get('reason') in {'state_capacity', 'storage_full', 'interrupted'}),
                'raw manifest completeness or retained length changed')
        return manifest

    def read_raw_chunk(self, reference, *, run_id, index):
        """Read one explicitly selected raw chunk without expanding the whole response."""
        manifest = self.read_raw_manifest(reference, run_id=run_id)
        require(manifest.get('format') == 'raw-evidence-v1' and isinstance(manifest.get('chunks'), list) and
                type(index) is int and 0 <= index < len(manifest['chunks']), 'invalid raw evidence cursor')
        chunk = manifest['chunks'][index]
        require(set(chunk) == {'run_id', 'kind', 'sha256', 'size'} and chunk['run_id'] == run_id and
                chunk['kind'] == 'raw-chunk' and type(chunk['size']) is int and
                0 < chunk['size'] <= 1024 * 1024, 'invalid raw chunk reference')
        part = self._read(self._path(chunk['sha256']), 1024 * 1024)
        require(len(part) == chunk['size'] and hashlib.sha256(part).hexdigest() == chunk['sha256'],
                'raw chunk changed', 'state_object_corrupt')
        return part

    def read_raw_evidence(self, reference, *, run_id):
        """Explicitly expand one complete raw result, within the evidence read budget."""
        manifest = self.read_raw_manifest(reference, run_id=run_id)
        require(manifest.get('format') == 'raw-evidence-v1' and manifest.get('complete') is True and
                type(manifest.get('size')) is int and 0 <= manifest['size'] <= EVIDENCE_LIMIT and
                isinstance(manifest.get('chunks'), list), 'raw evidence is incomplete or exceeds one expansion')
        data = bytearray()
        for chunk in manifest['chunks']:
            require(set(chunk) == {'run_id', 'kind', 'sha256', 'size'} and chunk['run_id'] == run_id and
                    chunk['kind'] == 'raw-chunk' and type(chunk['size']) is int and
                    0 < chunk['size'] <= 1024 * 1024, 'invalid raw chunk reference')
            part = self._read(self._path(chunk['sha256']), 1024 * 1024)
            require(len(part) == chunk['size'] and hashlib.sha256(part).hexdigest() == chunk['sha256'],
                    'raw chunk changed', 'state_object_corrupt')
            require(len(data) + len(part) <= manifest['size'], 'raw evidence length changed')
            data.extend(part)
        require(len(data) == manifest['size'] and hashlib.sha256(data).hexdigest() == manifest['sha256'],
                'raw evidence digest changed', 'state_object_corrupt')
        return json.loads(data)

    def read_evidence(self, reference, *, run_id, kind='evidence', maximum=None):
        require(isinstance(reference, dict) and set(reference) == {'run_id', 'kind', 'sha256', 'size'} and
                reference['run_id'] == run_id and reference['kind'] == kind and
                type(reference['size']) is int, 'cross-run or malformed object reference')
        limit = EVIDENCE_LIMIT if kind == 'evidence' else SUBJECT_LIMIT
        if maximum is not None:
            limit = min(limit, maximum)
        require(0 < reference['size'] <= limit, 'object reference exceeds its read limit', 'state_capacity')
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
        require(isinstance(body, dict) and body.get('format') == FORMAT and body.get('run_id') == run_id and body.get('kind') == kind,
                'object identity changed', 'state_object_corrupt')
        self._verified_objects[reference['sha256']] = copy.deepcopy(body)
        return body

    def open_current(self):
        if self._measured_current is not None:
            return self._measured_current
        if not self.checkpoint.exists():
            return None
        envelope = json.loads(self._read(self.checkpoint, ROOT_LIMIT))
        require(envelope.get('store_format') == FORMAT, 'retain original checkpoint runtime',
                'legacy_run_requires_original_runtime')
        root = self.read_evidence(envelope['current'], run_id=envelope['run_id'], kind='root')['value']
        require(root.get('revision') == envelope['revision'] and
                isinstance(root.get('subjects'), dict) and len(root['subjects']) + ('scope' in root) + ('delivery' in root) <= MAX_SUBJECTS,
                'invalid current root')
        self._run_id = envelope['run_id']
        return CurrentProjection(envelope['run_id'], envelope['revision'], root, envelope['current'])

    def read_ledger_control(self, pointer, *, selector=None):
        """Read a named ledger generation; never use the runner's current pointer."""
        require(isinstance(pointer, dict) and set(pointer) == {
            'format', 'ledger_path', 'topic_id', 'controller_ref', 'ledger_revision',
            'control_revision', 'reference'}, 'invalid ledger control pointer')
        require(pointer['format'] == 'ledger-control-v1' and
                pointer['ledger_path'] == str(self.checkpoint.resolve()) and
                type(pointer['ledger_revision']) is int and pointer['ledger_revision'] > 0 and
                type(pointer['control_revision']) is int and pointer['control_revision'] > 0,
                'ledger control authority changed')
        ref = pointer['reference']
        body = self.read_evidence(ref, run_id=ref['run_id'], kind='ledger-control')['value']
        require(isinstance(body, dict) and set(body) == {'identity', 'context', 'retired', 'receipt'} and
                body['identity'] == {k: v for k, v in pointer.items() if k != 'reference'},
                'ledger generation identity changed')
        context = body['context']
        require(context.get('controller_ref') == pointer['controller_ref'] and
                context.get('topic_ref') == pointer['topic_id'] and
                'retired_handoffs' not in context and isinstance(body['retired'], dict) and
                len(body['retired']) <= 32, 'invalid current ledger projection')
        if selector is not None:
            matches = [(key, value) for key, value in body['retired'].items()
                       if all((key if field == 'handoff_id' else value.get(field)) == expected
                              for field, expected in selector.items())]
            require(len(matches) <= 1, 'ambiguous retired ledger projection')
            for key, value in matches:
                retired = self.read_evidence(value['reference'], run_id=ref['run_id'], kind='ledger-handoff')['value']
                require(retired.get('handoff_id') == key, 'retired ledger identity changed')
                context['retired_handoffs'] = {key: retired['value']}
        require(self.bytes_read <= READ_LIMIT, 'ledger projection exceeded its read budget', 'state_capacity')
        return context, body

    def prepare_ledger_control(self, context, *, topic_id, ledger_revision, control_revision,
                               previous, receipt):
        """Prepare immutable objects only. The existing ledger transaction publishes them."""
        run_id = previous['reference']['run_id'] if previous else str(uuid.uuid4())
        retired = self.read_ledger_control(previous)[1]['retired'] if previous else {}
        # Construct current fields directly; no full context/history copy is externalized.
        current = {key: value for key, value in context.items() if key != 'retired_handoffs'}
        for key, value in context.get('retired_handoffs', {}).items():
            authority = value['plan'].get('entry_authority') or {}
            retired[key] = {'run_id': authority.get('run_id'), 'attempt_id': authority.get('attempt_id'),
                'delivery_digest': value['delivery_digest'],
                'reference': self.write_object(run_id, 'ledger-handoff', {'handoff_id': key, 'value': value})}
        require(len(retired) <= 32, 'current ledger handoff reference window is full', 'state_capacity')
        identity = {'format': 'ledger-control-v1', 'ledger_path': str(self.checkpoint.resolve()),
            'topic_id': topic_id, 'controller_ref': context['controller_ref'],
            'ledger_revision': ledger_revision, 'control_revision': control_revision}
        body = {'identity': identity, 'context': current, 'retired': retired, 'receipt': receipt}
        reference = self.write_object(run_id, 'ledger-control', body)
        pointer = {**identity, 'reference': reference}
        # Account for the original receipt before the ledger may commit. A crash
        # can overcount this reservation, but recovery never needs fresh budget.
        if receipt is not None:
            certificate = encode({'pointer': pointer, 'fingerprint': receipt['fingerprint'], 'result': receipt['result']})
            require(len(certificate) <= SUBJECT_LIMIT, 'ledger receipt exceeds its fixed budget', 'state_capacity')
            if self._measured_writes is not None:
                self._measured_writes[str(self.ledger_receipt_path(topic_id, receipt['key']))] = len(certificate)
            else:
                self._charge(len(certificate))
        return pointer

    def ledger_receipt_path(self, topic_id, key):
        digest = hashlib.sha256(encode([topic_id, key])).hexdigest()
        return self._safe(Path('ledger-receipts') / digest[:2] / digest)

    def certify_ledger_control(self, pointer):
        """Caller holds the ledger lock and has read this exact committed pointer."""
        _, body = self.read_ledger_control(pointer)
        receipt = body['receipt']
        if receipt is None:
            return
        path = self.ledger_receipt_path(pointer['topic_id'], receipt['key'])
        value = {'pointer': pointer, 'fingerprint': receipt['fingerprint'], 'result': receipt['result']}
        data = encode(value)
        require(len(data) <= SUBJECT_LIMIT, 'ledger receipt exceeds its fixed budget', 'state_capacity')
        if path.exists():
            require(self._read(path, SUBJECT_LIMIT) == data, 'original ledger receipt changed', 'state_object_corrupt')
        elif self._measured_writes is not None:
            self._measured_writes[str(path)] = len(data)
        else:
            atomic_write(path, data)

    def read_ledger_receipt(self, topic_id, key, fingerprint):
        path = self.ledger_receipt_path(topic_id, key)
        if not path.exists():
            return None
        value = json.loads(self._read(path, SUBJECT_LIMIT))
        require(value.get('fingerprint') == fingerprint, 'idempotency key was reused for another update', 'idempotency_conflict')
        pointer = value['pointer']
        require(pointer['topic_id'] == topic_id, 'ledger receipt topic changed')
        _, body = self.read_ledger_control(pointer)
        require(body['receipt'] == {'key': key, 'fingerprint': fingerprint, 'result': value['result']},
                'ledger receipt does not bind its original projection', 'state_object_corrupt')
        return {**value['result'], 'control': {**value['result']['control'], 'store_ref': pointer},
                'idempotent_replay': True}

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
        """Persist only verified stage facts, never an old current root."""
        require(isinstance(value, dict) and value.get('kind') in {'accepted-design-v1', 'accepted-implementation-v1'},
                'invalid predecessor facts')
        return self.write_object(current.run_id, 'predecessor', value)

    def read_predecessor(self, current, reference):
        value = self.read_evidence(reference, run_id=current.run_id, kind='predecessor')['value']
        require(isinstance(value, dict) and value.get('kind') in {'accepted-design-v1', 'accepted-implementation-v1'} and
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
        if self._measured_writes is not None:
            return
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
                receipt.get('request_digest') == raw_measure(request)[1],
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
                       scope=None, delivery=None):
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
        require(type(revision) is int and revision > actual and len(subjects) + (scope is not None) + (delivery is not None) <= MAX_SUBJECTS,
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
        if delivery is not None:
            require(isinstance(delivery, dict) and delivery.get('kind') == 'accepted-implementation-v1' and
                    delivery.get('source_revision') == metadata['revision'] and
                    delivery.get('stage_subject') == metadata['stage_subject'] and metadata['stage'] == 3 and
                    len(encode({'format': FORMAT, 'run_id': run_id, 'kind': 'predecessor',
                                'value': delivery})) <= SUBJECT_LIMIT,
                    'invalid current implementation delivery', 'state_capacity')
        references = {name: self.write_object(run_id, 'subject', value, subject_id=name)
                      for name, value in subjects.items()}
        require(len(receipts) <= 16, 'receipt commit batch exceeds its fixed proof budget', 'state_capacity')
        root = {'revision': revision, 'subjects': references, 'metadata': metadata, 'outer': outer,
                'receipts': list(receipts)}
        if scope is not None:
            root['scope'] = self.write_object(run_id, 'scope', scope_body)
        if delivery is not None:
            root['delivery'] = self.write_object(run_id, 'predecessor', delivery)
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
        if self._measured_writes is not None:
            for receipt in receipts:
                index = {**receipt, 'revision': revision, 'run_id': run_id, 'root': reference['sha256']}
                data = encode(index)
                require(len(data) <= 16 * 1024, 'receipt index exceeds its fixed budget', 'state_capacity')
                self._measured_writes[str(self._receipt_path(receipt['key']))] = len(data)
            envelope = {'store_format': FORMAT, 'run_id': run_id, 'revision': revision,
                        'current': reference, 'previous': current.reference if current else None,
                        'workflow_progress': metadata}
            require(len(encode(envelope)) <= ROOT_LIMIT, 'current root exceeds its fixed budget', 'state_capacity')
            self._measured_writes[str(self.checkpoint)] = len(encode(envelope))
            marker = {'revision': revision, 'root': reference['sha256'], 'receipts': list(receipts)}
            self._measured_writes[str(self._safe(Path('commits') / str(revision)))] = len(encode(marker))
            measured_current = CurrentProjection(run_id, revision, root, reference)
            if self._measure_sequence:
                self._measured_current = measured_current
            return measured_current
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
