import atexit, functools, json, os, resource, subprocess, sys, time
from pathlib import Path
_original = subprocess.run
_events = []
_start = False

def phase():
    value = os.environ.get('WORKFLOW_GIT_PROFILE_PHASE', 'runner')
    frame = sys._getframe(1)
    while frame:
        if '/unittest/case.py' in frame.f_code.co_filename:
            name = frame.f_code.co_name
            if name == '_callSetUp': value = 'setup'
            elif name == '_callTestMethod': value = 'test'
            elif name in {'_callCleanup','doCleanups','_callTearDown'}: value = 'cleanup'
        frame = frame.f_back
    return 'start' if _start else value

def run(*args, **kwargs):
    command = args[0] if args else kwargs.get('args', [])
    if not isinstance(command, (list, tuple)) or not command: return _original(*args, **kwargs)
    kind = 'git' if Path(str(command[0])).name == 'git' else 'child'
    current = phase()
    if kind == 'child':
        env = dict(kwargs.get('env') or os.environ)
        env['WORKFLOW_GIT_PROFILE_PHASE'] = current
        kwargs['env'] = env
    started = time.perf_counter()
    try: return _original(*args, **kwargs)
    finally:
        _events.append({'kind': kind, 'phase': current, 'arguments': [str(v) for v in command],
                        'seconds': time.perf_counter() - started})
subprocess.run = run

if '--worker' in sys.argv:
    sys.path.insert(0, str(Path(os.environ['WORKFLOW_GIT_PROFILE_REPOSITORY']) / 'src/shared/scripts'))
    import workflow_progress
    _start_original = workflow_progress.Progress.start
    @functools.wraps(_start_original)
    def start(*args, **kwargs):
        global _start
        _start = True
        try: return _start_original(*args, **kwargs)
        finally: _start = False
    workflow_progress.Progress.start = start

def save():
    root = os.environ.get('WORKFLOW_GIT_PROFILE_DIR')
    if root:
        own = resource.getrusage(resource.RUSAGE_SELF)
        child = resource.getrusage(resource.RUSAGE_CHILDREN)
        Path(root, str(os.getpid()) + '.json').write_text(json.dumps({'pid':os.getpid(), 'ppid':os.getppid(),
            'argv':sys.argv, 'events':_events, 'cpu_self':own.ru_utime+own.ru_stime,
            'cpu_children':child.ru_utime+child.ru_stime}))
atexit.register(save)
