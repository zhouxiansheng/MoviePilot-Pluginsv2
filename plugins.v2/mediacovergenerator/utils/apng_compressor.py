"""
APNG 统一调色板量化压缩 (V11 - K-means 调色板优化)
====================================================
纯 Python 实现，不依赖任何外部 C 二进制 (pngquant/apngopt)。

核心算法:
  1. 拆解 APNG，合成完整帧 (处理 blend/dispose)
  2. 全分辨率采样 → Pillow Median Cut + K-means 生成统一调色板
  3. 智能透明色: 动态选择未使用索引或合并最相似颜色
  4. NoDither 帧量化 (无噪点，K-means 从根源减色带)
  5. 重新组装 APNG，保留原始帧布局

quality 参数映射 (方案 A):
  0      → 跳过压缩
  1~100  → 颜色数 = max(2, min(256, int(2 + 254 * quality / 100)))
            K-means=5 始终启用，NoDither 始终启用
"""

import os
import shutil
import tempfile
from io import BytesIO

import numpy as np
from PIL import Image
from apng import APNG

from app.log import logger

# APNG 帧控制常量
_APNG_DISPOSE_OP_NONE = 0
_APNG_DISPOSE_OP_BACKGROUND = 1
_APNG_DISPOSE_OP_PREVIOUS = 2
_APNG_BLEND_OP_SOURCE = 0
_APNG_BLEND_OP_OVER = 1

# K-means 迭代次数 (调色板优化)
_KMEANS_ITERS = 5


def _disassemble_apng_composited(apng_path, out_dir):
    """拆解 APNG，并把每帧合成到完整画布上"""
    logger.info("APNG V11: 拆解并合成帧...")
    img = APNG.open(apng_path)

    first_ctrl = img.frames[0][1]
    if first_ctrl:
        canvas_w = first_ctrl.width
        canvas_h = first_ctrl.height
    else:
        first_png_buf = BytesIO()
        img.frames[0][0].save(first_png_buf)
        first_img = Image.open(first_png_buf)
        canvas_w, canvas_h = first_img.size

    logger.info("APNG V11: 画布 %sx%s, %s 帧" % (canvas_w, canvas_h, len(img.frames)))

    frames = []
    prev_canvas = None
    has_any_transparency = False

    for i, (png, control) in enumerate(img.frames):
        png_buf = BytesIO()
        png.save(png_buf)
        frame_img = Image.open(png_buf)
        if frame_img.mode != "RGBA":
            frame_img = frame_img.convert("RGBA")

        fw, fh = frame_img.size
        if control:
            fw = control.width
            fh = control.height

        if control and (control.x_offset > 0 or control.y_offset > 0 or fw < canvas_w or fh < canvas_h):
            if control.blend_op == _APNG_BLEND_OP_OVER:
                if prev_canvas is None:
                    prev_canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                composite = prev_canvas.copy()
                composite.paste(frame_img, (control.x_offset, control.y_offset), frame_img)
            else:
                composite = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                composite.paste(frame_img, (control.x_offset, control.y_offset))
        else:
            composite = frame_img

        arr_check = np.array(composite)
        if (arr_check[:, :, 3] < 128).any():
            has_any_transparency = True

        full_path = os.path.join(out_dir, "full_%04d.png" % i)
        composite.save(full_path, format="PNG")

        frames.append({
            'full_path': full_path,
            'control': control,
            'frame_w': fw,
            'frame_h': fh,
            'x_offset': control.x_offset if control else 0,
            'y_offset': control.y_offset if control else 0,
        })

        if control:
            if control.depose_op == _APNG_DISPOSE_OP_PREVIOUS:
                pass
            elif control.depose_op == _APNG_DISPOSE_OP_BACKGROUND:
                prev_canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            else:
                prev_canvas = composite.copy()
        else:
            prev_canvas = composite.copy()

    logger.info("APNG V11: 合成 %s 帧, 有透明像素: %s" % (len(frames), has_any_transparency))
    return frames, canvas_w, canvas_h, has_any_transparency


def _build_unified_palette(frames, canvas_w, n_colors):
    """全分辨率采样所有帧，用 Median Cut + K-means 生成统一调色板"""
    logger.info("APNG V11: 生成调色板 (MedianCut+K-means=%s, %s 色)..." % (_KMEANS_ITERS, n_colors))

    sample_arrays = []
    for frame_info in frames:
        frame_img = Image.open(frame_info['full_path']).convert("RGBA")
        arr = np.array(frame_img)
        alpha = arr[:, :, 3]
        mask = alpha > 0
        rgb = arr[:, :, :3][mask]
        if len(rgb) > 0:
            sample_arrays.append(rgb)

    all_pixels = np.vstack(sample_arrays)
    total_px = len(all_pixels)

    sample_h = (total_px + canvas_w - 1) // canvas_w
    pad_n = sample_h * canvas_w - total_px
    if pad_n > 0:
        all_pixels = np.vstack([all_pixels, np.zeros((pad_n, 3), dtype=np.uint8)])

    sample_img = Image.fromarray(all_pixels.reshape(sample_h, canvas_w, 3), "RGB")

    palette_img = sample_img.quantize(
        colors=n_colors,
        method=Image.Quantize.MEDIANCUT,
        kmeans=_KMEANS_ITERS,
        dither=Image.Dither.NONE,
    )

    logger.info("APNG V11: 调色板完成: %s 色" % n_colors)
    return palette_img


def _find_unused_palette_index(quantized_data_all_frames, n_colors):
    """统计索引使用情况，找出未使用的索引"""
    all_indices = np.concatenate([d.flatten() for d in quantized_data_all_frames])
    unique_used, counts = np.unique(all_indices, return_counts=True)

    used_set = set(unique_used.tolist())
    all_set = set(range(n_colors))
    unused = sorted(all_set - used_set)

    return unused, dict(zip(unique_used.tolist(), counts.tolist()))


def _find_mergeable_palette_indices(palette_np, index_usage_counts, n_colors):
    """找两个最相似的颜色合并，腾出索引给透明色"""
    pal_f = palette_np.astype(np.float32)

    best_dist = float('inf')
    best_i = -1
    best_j = -1

    for i in range(n_colors):
        for j in range(i + 1, n_colors):
            dist = np.sum((pal_f[i] - pal_f[j]) ** 2)
            if dist < best_dist:
                best_dist = dist
                best_i = i
                best_j = j

    ci = index_usage_counts.get(best_i, 0)
    cj = index_usage_counts.get(best_j, 0)
    if ci <= cj:
        return best_i, best_j
    else:
        return best_j, best_i


def _setup_transparency_smart(palette_img, palette_np, frames, has_transparency, n_colors):
    """智能设置透明色"""
    if not has_transparency:
        logger.info("APNG V11: 无透明像素，跳过透明色设置")
        return palette_img, palette_np, None, None

    logger.info("APNG V11: 智能设置透明色...")

    # 预量化所有帧，统计索引使用
    quantized_all = []
    for frame_info in frames:
        full_img = Image.open(frame_info['full_path']).convert("RGBA")
        arr = np.array(full_img)
        rgb_img = Image.fromarray(arr[:, :, :3], "RGB")
        quantized = rgb_img.quantize(palette=palette_img, dither=Image.Dither.NONE)
        quantized_all.append(np.array(quantized))

    unused_indices, usage_counts = _find_unused_palette_index(quantized_all, n_colors)

    if unused_indices:
        trans_idx = unused_indices[0]
        logger.info("APNG V11: 找到 %s 个未使用索引, 选 %s 作透明色" % (len(unused_indices), trans_idx))
        palette_np[trans_idx] = [0, 0, 0]

        new_pal_img = Image.new("P", (1, 1))
        new_pal_img.putpalette(palette_np.reshape(-1).tolist())
        new_pal_img.info["transparency"] = trans_idx
        return new_pal_img, palette_np, trans_idx, None
    else:
        logger.info("APNG V11: 全部索引被使用，合并最相似颜色...")
        merge_from, merge_to = _find_mergeable_palette_indices(palette_np, usage_counts, n_colors)

        for i in range(len(quantized_all)):
            mask = quantized_all[i] == merge_from
            quantized_all[i][mask] = merge_to

        palette_np[merge_from] = [0, 0, 0]
        trans_idx = merge_from

        new_pal_img = Image.new("P", (1, 1))
        new_pal_img.putpalette(palette_np.reshape(-1).tolist())
        new_pal_img.info["transparency"] = trans_idx

        logger.info("APNG V11: 索引 %s 已腾出作透明色" % trans_idx)
        return new_pal_img, palette_np, trans_idx, quantized_all


def _quantize_frames(frames, palette_img, palette_np, out_dir, transparent_index, n_colors, precomputed=None):
    """用统一调色板量化每一帧"""
    logger.info("APNG V11: 量化各帧 (NoDither)...")

    quantized_frames = []

    for idx, frame_info in enumerate(frames):
        full_img = Image.open(frame_info['full_path']).convert("RGBA")
        arr = np.array(full_img)

        if precomputed is not None:
            p_data = precomputed[idx].copy()
        else:
            rgb_arr = arr[:, :, :3]
            rgb_img = Image.fromarray(rgb_arr, "RGB")
            quantized = rgb_img.quantize(palette=palette_img, dither=Image.Dither.NONE)
            p_data = np.array(quantized)

        alpha_arr = arr[:, :, 3]

        if transparent_index is not None:
            transparent_mask = alpha_arr < 128
            if transparent_mask.any():
                p_data[transparent_mask] = transparent_index
                opaque_using_trans = (~transparent_mask) & (p_data == transparent_index)
                if opaque_using_trans.any():
                    replacement = (transparent_index + 1) % n_colors
                    p_data[opaque_using_trans] = replacement

            final_p = Image.fromarray(p_data, "P")
            final_p.putpalette(palette_np.reshape(-1).tolist())
            final_p.info["transparency"] = transparent_index
        else:
            final_p = Image.fromarray(p_data, "P")
            final_p.putpalette(palette_np.reshape(-1).tolist())

        x = frame_info['x_offset']
        y = frame_info['y_offset']
        fw = frame_info['frame_w']
        fh = frame_info['frame_h']

        if x > 0 or y > 0 or fw < final_p.size[0] or fh < final_p.size[1]:
            cropped = final_p.crop((x, y, x + fw, y + fh))
        else:
            cropped = final_p

        out_path = os.path.join(out_dir, "quant_%04d.png" % idx)
        cropped.save(out_path, format="PNG", optimize=True)

        quantized_frames.append({
            'path': out_path,
            'control': frame_info['control'],
            'x_offset': x,
            'y_offset': y,
            'frame_w': fw,
            'frame_h': fh,
        })

    logger.info("APNG V11: %s 帧量化完成" % len(quantized_frames))
    return quantized_frames


def _reassemble_apng(quantized_frames, output_path):
    """重新组装为 APNG，保留原始帧布局和控制信息"""
    logger.info("APNG V11: 重新组装 APNG...")

    out_apng = APNG()

    for frame_info in quantized_frames:
        ctrl = frame_info['control']
        out_apng.append_file(
            frame_info['path'],
            width=frame_info['frame_w'],
            height=frame_info['frame_h'],
            x_offset=frame_info['x_offset'],
            y_offset=frame_info['y_offset'],
            delay=ctrl.delay if ctrl else 100,
            delay_den=ctrl.delay_den if ctrl else 1000,
            depose_op=ctrl.depose_op if ctrl else 1,
            blend_op=ctrl.blend_op if ctrl else 0,
        )

    out_apng.save(output_path)
    logger.info("APNG V11: APNG 保存完成")


def compress_apng(input_path, output_path, quality=80):
    """
    V11 K-means 调色板优化压缩 APNG

    参数:
        input_path:  输入 APNG 文件路径
        output_path: 输出 APNG 文件路径
        quality:     0=不压缩, 1~100=映射到颜色数

    返回: (success: bool, error: str|None)
    """
    try:
        quality = max(0, min(100, int(quality)))
        if quality == 0:
            shutil.copy2(input_path, output_path)
            return True, None

        n_colors = max(2, min(256, int(2 + 254 * quality / 100)))

        input_size = os.path.getsize(str(input_path))
        logger.info("APNG V11: quality=%s, colors=%s, input=%.1fKB, Pillow=%s, numpy=%s" % (
            quality, n_colors, input_size / 1024, Image.__version__, np.__version__))

        # 备份压缩前的原始文件到插件数据目录（方便用户导出对比）
        try:
            backup_dir = None
            try:
                from app.core.config import settings
                backup_dir = os.path.join(str(settings.CONFIG_DIR), "plugins", "MediaCoverGenerator")
            except Exception:
                pass
            if backup_dir is None or not os.path.isdir(backup_dir):
                backup_dir = tempfile.gettempdir()
            raw_backup = os.path.join(backup_dir, "v11_raw_backup.apng")
            shutil.copy2(str(input_path), raw_backup)
            logger.info("APNG V11: 原始文件已备份到 %s (%.1fKB)" % (raw_backup, os.path.getsize(raw_backup) / 1024))
            logger.info("APNG V11: 可用 docker cp <容器名>:%s ./ 导出对比" % raw_backup)
        except Exception:
            pass

        tmpdir = tempfile.mkdtemp(prefix="apng_v11_")
        try:
            # Step 1: 拆解+合成
            frames, canvas_w, canvas_h, has_transparency = _disassemble_apng_composited(
                str(input_path), tmpdir)

            # Step 2: 调色板
            palette_img = _build_unified_palette(frames, canvas_w, n_colors)
            pal = palette_img.getpalette()
            # 补齐到 256 色 (Pillow 可能只返回实际颜色数)
            actual_colors = len(pal) // 3
            if actual_colors < 256:
                pal = pal + [0] * ((256 - actual_colors) * 3)
            palette_np = np.array(pal[:768]).reshape(256, 3).astype(np.uint8)

            # Step 3: 透明色
            palette_img, palette_np, trans_idx, precomputed = _setup_transparency_smart(
                palette_img, palette_np, frames, has_transparency, n_colors)

            # Step 4: 量化
            quantized_frames = _quantize_frames(
                frames, palette_img, palette_np, tmpdir, trans_idx, n_colors, precomputed)

            # Step 5: 重组
            _reassemble_apng(quantized_frames, str(output_path))

            output_size = os.path.getsize(str(output_path))
            ratio = (1 - output_size / input_size) * 100
            logger.info("APNG V11: %.1fKB -> %.1fKB (%.1f%%)" % (
                input_size / 1024, output_size / 1024, ratio))

            return True, None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    except Exception as e:
        logger.error("APNG V11 压缩失败: %s" % str(e))
        import traceback
        logger.error(traceback.format_exc())
        try:
            shutil.copy2(str(input_path), str(output_path))
        except Exception:
            pass
        return False, str(e)
