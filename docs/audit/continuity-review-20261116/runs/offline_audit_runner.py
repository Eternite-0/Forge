"""Audit-only offline verification; all writes stay beside this script."""
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True


def guard(event, args):
    if event == 'socket.getaddrinfo':
        host = args[0]
    elif event == 'socket.connect':
        address = args[1]
        if isinstance(address, (str, bytes)):
            return  # Local Unix-domain socket.
        host = address[0]
    else:
        return
    if host in ('localhost', b'localhost'):
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except (ValueError, TypeError):
        pass
    raise PermissionError('Audit forbids non-loopback networking')


def snapshot(label):
    diff = subprocess.check_output(['git', 'diff', '--binary', 'HEAD'], cwd=ROOT)
    (OUT / (label + '.diff')).write_bytes(diff)
    paths = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=ROOT)
    files = {}
    for name in paths.decode().splitlines():
        data = (ROOT / name).read_bytes()
        files[name] = hashlib.sha256(data).hexdigest()
    info = {
        'captured_at': datetime.datetime.now().astimezone().isoformat(),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'diff_sha256': hashlib.sha256(diff).hexdigest(),
        'files': files,
    }
    (OUT / (label + '.json')).write_text(json.dumps(info, indent=2) + '\n')
    return info


if __name__ == '__main__':
    mode = 'followup' if '--followup' in sys.argv else ('safety' if '--safety' in sys.argv else 'offline')
    sys.addaudithook(guard)
    # Explicitly remove ambient real-provider credentials/configuration.
    for name in list(os.environ):
        if name.startswith(('FORGE_PROVIDER', 'OPENAI_', 'ANTHROPIC_', 'DEEPSEEK_',
                            'AZURE_OPENAI_', 'FORGE_ROUTER_MODEL')):
            os.environ.pop(name)
    files = [
        'test_capability_router_projects.py',
        'test_composer_resources_v21.py',
        'test_core_measured_planner_v19.py',
        'test_core_measured_serial_v17.py',
        'test_planner_dataflow_v21.py',
        'test_planner_guidance_v24.py',
        'test_planner_protocol_v23.py',
        'test_planner_protocol_v25.py',
        'test_router_json_protocol.py',
        'test_superagent_loop.py',
    ]
    if mode == 'safety':
        files = [
            'test_live_core_private_control.py',
            'test_capability_continuation_gates.py',
            'test_capability_continuity_evidence.py',
            'test_recall_archive_dependencies_superagent.py',
            'test_recall_chain_superagent.py',
            'test_worker_continuation_completion.py',
            'test_worker_core_provenance_superagent.py',
            'test_reviewer_capacity_phase2.py',
            'test_superagent_events.py',
        ]
    if mode == 'followup':
        files = [
            'test_worker_core_provenance_superagent.py',
            'test_worker_continuation_completion.py',
            'test_continuation_artifact_review.py',
            'test_recall_restart_continuity.py',
            'test_continuity_minimal_probe.py',
            'test_router_json_protocol.py',
        ]
    os.environ['PYTHONPATH'] = os.pathsep.join([str(OUT), str(ROOT / 'backend' / 'src')])
    os.chdir(ROOT)
    before = snapshot(mode + '-before')
    sys.path.insert(0, str(ROOT / 'backend' / 'src'))
    import pytest
    code = pytest.main([
        '-q', '-p', 'no:cacheprovider',
        '--basetemp=' + str(OUT / (mode + '-pytest-temp')),
        '--junitxml=' + str(OUT / (mode + '-fixtures.xml')),
        *['backend/tests/' + f for f in files],
    ])
    after = snapshot(mode + '-after')
    (OUT / (mode + '-result.json')).write_text(json.dumps({
        'exit_code': int(code),
        'same_diff_before_after': before['diff_sha256'] == after['diff_sha256'],
        'network_guard': 'non-loopback DNS and connects denied in audit process',
        'provider_credentials': 'selected real-provider environment prefixes removed',
        'scope': files,
    }, indent=2) + '\n')
    raise SystemExit(code)
