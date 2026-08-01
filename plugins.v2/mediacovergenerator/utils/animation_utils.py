"""
集中管理动画相关的工具函数，避免在多个 style 文件中重复定义。
"""
import hashlib
import math
from pathlib import Path

from PIL import Image, ImageOps


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _ease_in_out_sine(t):
    t = _clamp(t, 0.0, 1.0)
    return 0.5 * (1.0 - math.cos(math.pi * t))


def _ease_out_back(t, overshoot=0.35):
    t = _clamp(t, 0.0, 1.0)
    u = t - 1.0
    return 1.0 + (overshoot + 1.0) * (u ** 3) + overshoot * (u ** 2)


def _blend_rgba(a, b, t):
    t = _clamp(t, 0.0, 1.0)
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b
    return Image.blend(a, b, t)


def _image_signature(image_path):
    """计算图片的内容签名，用于去重。"""
    try:
        with Image.open(image_path) as im:
            sig_img = ImageOps.fit(im.convert("L"), (24, 24), method=Image.Resampling.BILINEAR)
            return hashlib.md5(sig_img.tobytes()).hexdigest()
    except Exception:
        return f"path:{Path(image_path).name.lower()}"


def _lerp(a, b, t):
    return a + (b - a) * t


def _wrap_english(draw, text, font, max_width):
    """将英文文本按空格换行，每个单词独占一行。"""
    if not text:
        return []
    if " " not in text:
        return [text]
    return [w for w in text.split(" ") if w]
