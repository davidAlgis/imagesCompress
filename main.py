#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
images_compress.py
──────────────────
• PNG  → JPEG (optional resize)
• GIF  → palette / FPS / resize with adaptive tuning
• NEW:  debug output is always ON (no --debug flag)
• NEW:  -t / --target_compression_factor  0 < factor ≤ 1   (default 1.0)
• tqdm bars: per-file and per-frame
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path
from typing import Tuple

import tqdm
from PIL import Image, ImageSequence


# ───────────────────────── helpers ──────────────────────────────────────────
def human_delta(o: int, n: int) -> str:
    d = o - n
    pct = abs(d) / o * 100 if o else 0
    sign = "−" if d > 0 else "+"
    return f"{o//1024} KB → {n//1024} KB ({sign}{pct:.1f} %)"


def calc_resize(
    size: Tuple[int, int],
    scale: float | None,
    max_w: int | None,
    max_h: int | None,
) -> Tuple[int, int]:
    w, h = size
    if scale and scale < 1:
        return max(1, int(w * scale)), max(1, int(h * scale))
    if max_w or max_h:
        ratio = min(
            (max_w / w) if max_w else 1, (max_h / h) if max_h else 1, 1
        )
        return max(1, int(w * ratio)), max(1, int(h * ratio))
    return w, h


# ─────────────────────── PNG → JPEG ─────────────────────────────────────────
def convert_png_to_jpeg(
    src: Path,
    dst: Path,
    *,
    quality: int = 80,
    bg=(255, 255, 255),
    scale: float | None = None,
    max_w: int | None = None,
    max_h: int | None = None,
) -> Tuple[bool, int, int]:
    im = Image.open(src)

    new_w, new_h = calc_resize(im.size, scale, max_w, max_h)
    if (new_w, new_h) != im.size:
        im = im.resize((new_w, new_h), Image.LANCZOS)

    if im.mode in ("RGBA", "LA") or (
        im.mode == "P" and "transparency" in im.info
    ):
        bg_img = Image.new("RGB", im.size, bg)
        if im.mode in ("RGBA", "LA"):
            bg_img.paste(im, mask=im.split()[-1])
        else:
            im = im.convert("RGBA")
            bg_img.paste(im, mask=im.split()[-1])
        im = bg_img
    else:
        im = im.convert("RGB")

    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    im.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)

    orig, new = src.stat().st_size, Path(tmp).stat().st_size
    if new < orig:
        shutil.move(tmp, dst)
        return True, orig, new
    Path(tmp).unlink()
    shutil.copy2(src, dst.with_suffix(".png"))
    return False, orig, orig


# ─────── GIF  one-pass compression (used inside adaptive wrapper) ───────────
def _compress_gif_once(
    src: Path,
    dst_tmp: str,
    *,
    colors: int,
    fps_target: int | None,
    scale: float | None,
    max_w: int | None,
    max_h: int | None,
    dither=False,
    bg=(255, 255, 255),
    show_bar: bool = True,
) -> int:
    gif = Image.open(src)

    new_w, new_h = calc_resize(gif.size, scale, max_w, max_h)

    first = gif.convert("RGBA")
    if (new_w, new_h) != first.size:
        first = first.resize((new_w, new_h), Image.LANCZOS)
    base = Image.new("RGB", (new_w, new_h), bg)
    base.paste(first, mask=first.split()[3])
    gpal = base.quantize(colors=colors, method=Image.FASTOCTREE)

    frames, durs = [], []
    iterator = ImageSequence.Iterator(gif)
    total = getattr(gif, "n_frames", None) or sum(1 for _ in iterator)
    iterator = ImageSequence.Iterator(gif)

    iterator2 = (
        tqdm.tqdm(
            iterator, total=total, leave=False, desc=f"Frames [{src.name}]"
        )
        if show_bar
        else iterator
    )
    for fr in iterator2:
        dur = fr.info.get("duration", 100)
        if (new_w, new_h) != fr.size:
            fr = fr.resize((new_w, new_h), Image.LANCZOS)
        rgba = fr.convert("RGBA")
        rgb = Image.new("RGB", (new_w, new_h), bg)
        rgb.paste(rgba, mask=rgba.split()[3])
        q = rgb.quantize(
            palette=gpal, dither=Image.FLOYDSTEINBERG if dither else Image.NONE
        )
        frames.append(q)
        durs.append(dur)

    if fps_target and fps_target > 0:
        ms_goal = 1000 / fps_target
        new_frames, new_durs, acc = [], [], 0
        for f, d in zip(frames, durs):
            acc += d
            if acc >= ms_goal:
                new_frames.append(f)
                new_durs.append(int(acc))
                acc = 0
        if new_frames:
            frames, durs = new_frames, new_durs

    frames[0].save(
        dst_tmp,
        save_all=True,
        append_images=frames[1:],
        loop=gif.info.get("loop", 0),
        duration=durs,
        optimize=True,
        disposal=2,
    )
    return Path(dst_tmp).stat().st_size


# ─────────────────────── GIF  adaptive wrapper ─────────────────────────────
def compress_gif(
    src: Path,
    dst: Path,
    *,
    colors: int,
    fps: int | None,
    scale: float | None,
    max_w: int | None,
    max_h: int | None,
    target_factor: float,
) -> Tuple[bool, int, int, dict]:
    """
    Halve colours → FPS → scale until
        new/original ≤ target_factor
    Always prints debug lines.
    Returns (used_new_file, orig_bytes, new_bytes, final_params_dict)
    """
    orig_size = src.stat().st_size

    min_colors = 8
    min_fps = 1
    min_scale = 0.1

    attempt = {"colors": colors or 256, "fps": fps, "scale": scale or 1.0}

    while True:
        fd, tmp = tempfile.mkstemp(suffix=".gif")
        os.close(fd)
        new_size = _compress_gif_once(
            src,
            tmp,
            colors=attempt["colors"],
            fps_target=attempt["fps"],
            scale=attempt["scale"],
            max_w=max_w,
            max_h=max_h,
            show_bar=False,
        )
        factor = new_size / orig_size

        print(
            f"  ↳ try colors={attempt['colors']:>3}, "
            f"fps={'orig' if attempt['fps'] is None else attempt['fps']:>4}, "
            f"scale={attempt['scale']:.3f}  → {new_size//1024} KB "
            f"(factor {factor:.3f})"
        )

        if factor <= target_factor:
            shutil.move(tmp, dst)
            return True, orig_size, new_size, attempt

        Path(tmp).unlink()

        if attempt["colors"] > min_colors:
            attempt["colors"] = max(min_colors, attempt["colors"] // 2)
            continue
        if attempt["fps"] is None:
            attempt["fps"] = 30
            continue
        if attempt["fps"] > min_fps:
            attempt["fps"] = max(min_fps, attempt["fps"] // 2)
            continue
        if attempt["scale"] > min_scale:
            attempt["scale"] = max(min_scale, attempt["scale"] / 2)
            continue

        shutil.copy2(src, dst)
        return False, orig_size, orig_size, attempt


# ─────────────────────── dispatcher ─────────────────────────────────────────
def process(
    path: Path,
    out_dir: Path | None,
    q: int,
    cols: int,
    fps: int | None,
    scale: float | None,
    mw: int | None,
    mh: int | None,
    tgt: float,
):
    base, ext = path.stem, path.suffix.lower()
    out_dir = out_dir or path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if ext == ".png":
        dst = out_dir / f"{base}_compressed.jpg"
        used, o, n = convert_png_to_jpeg(
            path, dst, quality=q, scale=scale, max_w=mw, max_h=mh
        )
        print(
            f"[PNG] {path.name:20s} {('converted' if used else 'kept original'):18s} "
            f"{human_delta(o,n)}"
        )

    elif ext == ".gif":
        dst = out_dir / f"{base}_compressed.gif"
        used, o, n, final = compress_gif(
            path,
            dst,
            colors=cols,
            fps=fps,
            scale=scale,
            max_w=mw,
            max_h=mh,
            target_factor=tgt,
        )
        print(
            f"[GIF] {path.name:20s} {('compressed' if used else 'kept original'):18s} "
            f"{human_delta(o,n)}"
        )
        print(
            f"      final parameters → colors={final['colors']}, "
            f"fps={'orig' if final['fps'] is None else final['fps']}, "
            f"scale={final['scale']:.3f}"
        )

    else:
        print(f"Skipping unsupported {path.name}")


# ─────────────────────── CLI / main ─────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Shrink PNG & GIF with resize, palette & FPS "
        "until target compression factor is reached."
    )
    ap.add_argument("-i", "--input", required=True, help="File or directory")
    ap.add_argument(
        "-o", "--output", help="Output directory (default: next to input)"
    )
    ap.add_argument(
        "-q", "--quality", type=int, default=80, help="JPEG quality (80)"
    )
    ap.add_argument(
        "--colors", type=int, default=128, help="GIF palette size start (128)"
    )
    ap.add_argument(
        "--fps", type=int, help="Target GIF FPS (will halve if needed)"
    )
    ap.add_argument(
        "--scale", type=float, help="Scale factor (e.g. 0.5 halves W&H)"
    )
    ap.add_argument("--max-width", type=int, help="Resize so width ≤ this")
    ap.add_argument("--max-height", type=int, help="Resize so height ≤ this")
    ap.add_argument(
        "-t",
        "--target_compression_factor",
        type=float,
        default=1.0,
        help="Desired size/original ratio (0<factor≤1, default 1.0)",
    )
    args = ap.parse_args()

    if not (0 < args.target_compression_factor <= 1):
        raise ValueError("--target_compression_factor must be in (0,1]")

    src = Path(args.input)
    out_dir = Path(args.output).resolve() if args.output else None

    if src.is_file():
        process(
            src,
            out_dir,
            args.quality,
            args.colors,
            args.fps,
            args.scale,
            args.max_width,
            args.max_height,
            args.target_compression_factor,
        )
        return

    if src.is_dir():
        files = [
            p for p in src.iterdir() if p.suffix.lower() in (".png", ".gif")
        ]
        if not files:
            print("No PNG/GIF files found.")
            return
        print(f"Found {len(files)} image(s).")
        for p in tqdm.tqdm(files, desc="Images"):
            process(
                p,
                out_dir,
                args.quality,
                args.colors,
                args.fps,
                args.scale,
                args.max_width,
                args.max_height,
                args.target_compression_factor,
            )
        return

    print(f"Error: '{src}' is neither a file nor a directory.")


if __name__ == "__main__":
    main()
