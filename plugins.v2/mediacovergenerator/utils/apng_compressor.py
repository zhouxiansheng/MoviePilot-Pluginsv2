import os, stat, subprocess, shutil, platform, logging, tempfile, glob
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

def _compress_apng_pil(input_path, output_path, quality=80):
    from PIL import Image
    tmpdir = tempfile.mkdtemp(prefix='apng_')
    try:
        extract_pattern = os.path.join(tmpdir, 'frame_%04d.png')
        r = subprocess.run(['ffmpeg', '-i', str(input_path), '-vsync', '0', extract_pattern],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if r.returncode != 0:
            return False, 'frame extraction failed'
        n_colors = max(2, min(256, int(2 + 254 * quality / 100)))
        frames = sorted(glob.glob(os.path.join(tmpdir, 'frame_*.png')))
        if not frames:
            return False, 'no frames extracted'
        for fp in frames:
            img = Image.open(fp)
            if img.mode == 'RGBA':
                alpha = img.split()[3]
                rgb = img.convert('RGB')
                q = rgb.quantize(colors=n_colors, method=2, dither=1)
                q = q.convert('RGBA')
                q.putalpha(alpha)
            else:
                q = img.quantize(colors=n_colors, method=2, dither=1)
            q.save(fp, format='PNG')
        tmp_out = os.path.join(tmpdir, 'output.png')
        r2 = subprocess.run(['ffmpeg', '-i', extract_pattern, '-vcodec', 'apng',
                             '-pix_fmt', 'rgba', '-plays', '0', '-f', 'apng', tmp_out],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if r2.returncode != 0:
            return False, 'APNG reassembly failed'
        ao = _get_binary('apngopt')
        if ao and _is_supported_arch():
            r3 = subprocess.run([ao, tmp_out, str(output_path), '-z2'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            if r3.returncode != 0:
                shutil.copy2(tmp_out, output_path)
        else:
            shutil.copy2(tmp_out, output_path)
        return True, None
    except subprocess.TimeoutExpired:
        return False, 'timeout >300s'
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def compress_apng(input_path, output_path, quality=80, use_apngopt=True):
    if quality > 0:
        return _compress_apng_pil(input_path, output_path, quality)
    if use_apngopt and _is_supported_arch():
        ao = _get_binary('apngopt')
        if ao:
            try:
                r = subprocess.run([ao, str(input_path), str(output_path), '-z2'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                if r.returncode == 0:
                    return True, None
            except Exception:
                pass
    shutil.copy2(input_path, output_path)
    return True, None
