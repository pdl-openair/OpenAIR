"""Debug run_hpc.sh directly."""
import subprocess, sys, re
sys.path.insert(0, 'skills/materials/vasp-stability-durable/scripts')
from run_stability import _jcc_env, _jcc_bin, _jcc_login, _repo_root
from pathlib import Path

env = _jcc_env()
token = _jcc_login(env)
env['JCC_TOKEN'] = token
print('token len:', len(token or ''))

jcc_dir = str(Path(_jcc_bin()).parent)
jq_dir = str(_repo_root() / 'skills' / 'materials' / 'jcc')

def to_msys(p):
    p = str(p).replace('\\', '/')
    m = re.match(r'^([A-Za-z]):(.*)', p)
    return f'/{m.group(1).lower()}{m.group(2)}' if m else p

hpc_env = {**env, 'PATH': f'{to_msys(jcc_dir)}:{to_msys(jq_dir)}:{env.get("PATH","")}'}
run_hpc_sh = to_msys(str(_repo_root() / 'skills/materials/vasp-thermodynamic-stability/scripts/run_hpc.sh'))
# Use already-computed relax dir from previous test run
local_path = to_msys(str(_repo_root() / 'outputs/canonical-materials/test-s7-mgo2/test-wu-mgo2-03/vasp-stability/competing_phases/MgO2_12at/relax'))
output_dir = to_msys(str(_repo_root() / 'outputs/test-mgo2-hpc-run2'))

print('run_hpc_sh:', run_hpc_sh)
print('local_path:', local_path)

r = subprocess.run(
    ['bash', run_hpc_sh,
     '--local-path', local_path,
     '--output-dir', output_dir,
     '--task-name', 'MgO2_12at_relax_v6'],
    capture_output=True, env=hpc_env, cwd=str(_repo_root()), timeout=300,
)
out = r.stdout.decode('utf-8', errors='replace')
err = r.stderr.decode('utf-8', errors='replace')
print('rc:', r.returncode)
print('--- stdout ---')
print(out)
if err:
    print('--- stderr ---')
    print(err[-500:])
