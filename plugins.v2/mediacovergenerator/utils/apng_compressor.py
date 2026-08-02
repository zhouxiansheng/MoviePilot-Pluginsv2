import os, stat, subprocess, shutil, platform, logging
from pathlib import Path

logger = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).parent.parent
BIN_DIR = PLUGIN_DIR / 'bin'

def _ensure_executable(path):
    try:
        m = os.stat(path).st_mode
        os.chmod(path, m | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

def _get_binary(name):
    bp = BIN_DIR / name
    if not bp.exists():
        return None
    _ensure_executable(str(bp))
    return str(bp)

def _is_supported_arch():
    return platform.machine().lower() in ('x86_64', 'amd64', 'x64')

def compress_apng(input_path, output_path, quality=80, use_apngopt=True):
    if not _is_supported_arch():
        return False, 'Unsupported arch'
    aq = _get_binary('apngquant')
    ao = _get_binary('apngopt')
    if quality > 0 and not aq:
        return False, 'apngquant not found'
    if use_apngopt and not ao:
        return False, 'apngopt not found'
    tmp = str(output_path) + '.tmp.png'
    try:
        if quality > 0 and aq:
            cmd = [aq, str(input_path), '--output', tmp, '--force', '--quality=0-' + str(quality)]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            if r.returncode not in (0, 15):
                return False, 'apngquant failed'
            qout = tmp
        else:
            qout = str(input_path)
        if use_apngopt and ao:
            cmd2 = [ao, qout, str(output_path), '-z2']
            r2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            if r2.returncode != 0:
                return False, 'apngopt failed'
        else:
            shutil.copy2(qout, output_path)
        if os.path.exists(tmp):
            os.remove(tmp)
        return True, None
    except subprocess.TimeoutExpired:
        return False, 'timeout'
    except Exception as e:
        return False, str(e)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass
