import collections, json, os, subprocess, sys, tempfile, time
from pathlib import Path
repository = Path(sys.argv[1]).resolve()
profile = Path(__file__).resolve().parent
output = Path(tempfile.mkdtemp(prefix='workflow-formal-git-profile-'))
env = dict(os.environ, PYTHONPATH=str(profile), WORKFLOW_GIT_PROFILE_DIR=str(output),
           WORKFLOW_GIT_PROFILE_REPOSITORY=str(repository))
started = time.perf_counter()
result = subprocess.run([sys.executable, '-B', str(repository/'scripts/run_tests.py'),
    '--test-file', 'tests/runtime/test_workflow_progress.py', '--match',
    'ProgressTests.test_launch_is_saved_before_host_and_never_reissued', '--jobs','1'],
    cwd=repository, env=env, text=True, capture_output=True)
(output/'runner-output.txt').write_text(result.stdout+result.stderr)
if result.returncode:
    print(result.stdout+result.stderr); print(output); raise SystemExit(result.returncode)
processes = [json.loads(p.read_text()) for p in output.glob('*.json')]
worker = next(p for p in processes if '--worker' in p['argv'])
children = [p for p in processes if p['ppid']==worker['pid']]
phases = collections.Counter(e['phase'] for p in [worker,*children] for e in p['events'] if e['kind']=='git')
summary = {'git_calls':sum(phases.values()), 'phases':dict(phases),
           'cpu_seconds':worker['cpu_self']+worker['cpu_children'], 'wall_seconds':time.perf_counter()-started,
           'python_child_processes':len(children)}
(output/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary));print(output)
