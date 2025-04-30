#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
images_compress.py
──────────────────
• PNG → JPEG  (optional resize)
• GIF → global palette + resize with adaptive tuning
    - colours halved first; if gain <5 % jump to resolution halving
• Always prints tuning steps & final parameters
• Loading bars: per-file (folder mode) + per-frame (each GIF attempt)
• -t/--target_compression_factor    0<factor≤1   (default 1.0)
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


# ───────── helpers ──────────────────────────────────────────────────────────
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


# ───────── PNG → JPEG ───────────────────────────────────────────────────────
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

    nw, nh = calc_resize(im.size, scale, max_w, max_h)
    if (nw, nh) != im.size:
        im = im.resize((nw, nh), Image.LANCZOS)

    if im.mode in ("RGBA", "LA") or (
        im.mode == "P" and "transparency" in im.info
    ):
        can = Image.new("RGB", im.size, bg)
        if im.mode in ("RGBA", "LA"):
            can.paste(im, mask=im.split()[-1])
        else:
            im = im.convert("RGBA")
            can.paste(im, mask=im.split()[-1])
        im = can
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


# ───────── GIF one-shot compressor (palette + optional resize) ──────────────
def _compress_gif_once(
    src: Path,
    tmp_out: str,
    *,
    colors: int,
    scale: float,
    max_w: int | None,
    max_h: int | None,
    bg=(255, 255, 255),
) -> int:
    gif = Image.open(src)
    nw, nh = calc_resize(gif.size, scale, max_w, max_h)

    first = gif.convert("RGBA")
    if (nw, nh) != first.size:
        first = first.resize((nw, nh), Image.LANCZOS)
    base = Image.new("RGB", (nw, nh), bg)
    base.paste(first, mask=first.split()[3])
    gpal = base.quantize(colors=colors, method=Image.FASTOCTREE)

    frames, durs = [], []
    iterator = ImageSequence.Iterator(gif)
    total = getattr(gif, "n_frames", None) or sum(1 for _ in iterator)
    iterator = ImageSequence.Iterator(gif)

    for fr in tqdm.tqdm(
        iterator, total=total, leave=False, desc=f"Frames [{src.name}]"
    ):
        dur = fr.info.get("duration", 100)
        if (nw, nh) != fr.size:
            fr = fr.resize((nw, nh), Image.LANCZOS)
        rgba = fr.convert("RGBA")
        rgb = Image.new("RGB", (nw, nh), bg)
        rgb.paste(rgba, mask=rgba.split()[3])
        q = rgb.quantize(palette=gpal, dither=Image.NONE)
        frames.append(q)
        durs.append(dur)

    frames[0].save(
        tmp_out,
        save_all=True,
        append_images=frames[1:],
        loop=gif.info.get("loop", 0),
        duration=durs,
        optimize=True,
        disposal=2,
    )
    return Path(tmp_out).stat().st_size


# ───────── adaptive GIF compressor (colours → resolution) ───────────────────
def compress_gif(
    src: Path,
    dst: Path,
    *,
    start_colors: int,
    start_scale: float,
    max_w: int | None,
    max_h: int | None,
    target: float,
) -> Tuple[bool, int, int, dict]:
    """
    1. keep halving colours until
         • gain per halving <5 %   or   size/orig ≤ target
    2. then halve scale until size/orig ≤ target  (or 10 % size hit)
    always prints debug info.
    """
    orig_size = src.stat().st_size
    attempt = {"colors": start_colors or 256, "scale": start_scale or 1.0}
    min_colors, min_scale = 8, 0.1
    prev_size = orig_size

    while True:
        fd, tmp = tempfile.mkstemp(suffix=".gif")
        os.close(fd)
        new_size = _compress_gif_once(
            src,
            tmp,
            colors=attempt["colors"],
            scale=attempt["scale"],
            max_w=max_w,
            max_h=max_h,
        )
        factor = new_size / orig_size
        gain = (prev_size - new_size) / orig_size
        print(
            f"  ↳ try colors={attempt['colors']:>3}, "
            f"scale={attempt['scale']:.3f} → {new_size//1024} KB "
            f"(factor {factor:.3f}, gain {gain:.3f})"
        )

        if factor <= target:
            shutil.move(tmp, dst)
            return True, orig_size, new_size, attempt

        Path(tmp).unlink()

        # decide next move
        if attempt["colors"] > min_colors and gain >= 0.05:
            attempt["colors"] = max(min_colors, attempt["colors"] // 2)
            prev_size = new_size
            continue

        if attempt["scale"] > min_scale:
            attempt["scale"] = max(min_scale, attempt["scale"] / 2)
            prev_size = new_size
            continue

        # nothing could reach target
        shutil.copy2(src, dst)
        return False, orig_size, orig_size, attempt


# ───────── dispatcher ───────────────────────────────────────────────────────
def process(
    img: Path,
    out_dir: Path | None,
    quality: int,
    colors: int,
    scale: float | None,
    mw: int | None,
    mh: int | None,
    target: float,
):
    base, ext = img.stem, img.suffix.lower()
    out = out_dir or img.parent
    out.mkdir(parents=True, exist_ok=True)

    if ext == ".png":
        dst = out / f"{base}_compressed.jpg"
        ok, o, n = convert_png_to_jpeg(
            img, dst, quality=quality, scale=scale, max_w=mw, max_h=mh
        )
        print(
            f"[PNG] {img.name:20s} {('converted' if ok else 'kept original'):18s} "
            f"{human_delta(o,n)}"
        )

    elif ext == ".gif":
        dst = out / f"{base}_compressed.gif"
        ok, o, n, final = compress_gif(
            img,
            dst,
            start_colors=colors,
            start_scale=scale or 1.0,
            max_w=mw,
            max_h=mh,
            target=target,
        )
        print(
            f"[GIF] {img.name:20s} {('compressed' if ok else 'kept original'):18s} "
            f"{human_delta(o,n)}"
        )
        print(
            f"      final parameters → colors={final['colors']}, "
            f"scale={final['scale']:.3f}"
        )

    else:
        print(f"Skipping unsupported {img.name}")


# ───────── CLI / main ───────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Shrink PNG/GIF via palette + resolution until target factor."
    )
    ap.add_argument("-i", "--input", required=True, help="File or directory")
    ap.add_argument("-o", "--output", help="Output directory")
    ap.add_argument(
        "-q", "--quality", type=int, default=80, help="JPEG quality (80)"
    )
    ap.add_argument(
        "--colors", type=int, default=128, help="Start GIF colours (128)"
    )
    ap.add_argument(
        "--scale", type=float, help="Start scale factor (0<factor≤1)"
    )
    ap.add_argument("--max-width", type=int, help="Resize so width ≤ value")
    ap.add_argument("--max-height", type=int, help="Resize so height ≤ value")
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
        for f in tqdm.tqdm(files, desc="Images"):
            process(
                f,
                out_dir,
                args.quality,
                args.colors,
                args.scale,
                args.max_width,
                args.max_height,
                args.target_compression_factor,
            )
        return

    print(f"Error: '{src}' is neither a file nor directory.")


if __name__ == "__main__":
    main()
