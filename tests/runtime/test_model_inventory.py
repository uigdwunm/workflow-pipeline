"""Account model discovery through a bounded local app-server response."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/shared/scripts'))
import model_inventory


class ModelInventoryTests(unittest.TestCase):
    def test_paginates_the_current_account_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = Path(temporary) / 'codex-stub'
            server.write_text('''#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    if request['method'] == 'initialize':
        response = {'id': request['id'], 'result': {}}
    else:
        if request['params'].get('includeHidden') is not False:
            print(json.dumps({'id': request['id'], 'error': {'message': 'hidden requested'}}), flush=True)
            continue
        cursor = request['params'].get('cursor')
        model = {'model': 'gpt-6-sol' if cursor is None else 'gpt-6-luna',
                 'supportedReasoningEfforts': [
                     {'reasoningEffort': 'high', 'description': 'fixture'}]}
        response = {'id': request['id'], 'result': {
            'data': [model, {'model': 'internal-' + str(cursor), 'hidden': True}],
            'nextCursor': 'second' if cursor is None else None}}
    print(json.dumps(response), flush=True)
''')
            server.chmod(0o755)
            self.assertEqual(model_inventory.available_pairs(str(server)),
                             frozenset({('gpt-6-sol', 'high'), ('gpt-6-luna', 'high')}))

    def test_explicit_catalog_error_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = Path(temporary) / 'codex-stub'
            server.write_text('''#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    response = ({'id': request['id'], 'result': {}}
                if request['method'] == 'initialize'
                else {'id': request['id'], 'error': {'message': 'unavailable'}})
    print(json.dumps(response), flush=True)
''')
            server.chmod(0o755)
            with self.assertRaises(model_inventory.ModelInventoryError):
                model_inventory.available_pairs(str(server))


if __name__ == '__main__':
    unittest.main()
