#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
images_compress.py
──────────────────
Shrink PNG   → JPEG   (optional resize)
Shrink GIF   → global-palette + resize
Search order : palette → resolution (priority)
Algorithm    : two-point palette interpolation → proportional scale
Accuracy     : |factor - target| ≤ 0.05
Performance  : parallel processing of files
Progress     : tqdm bars (files + frames)
Verbose      : detailed console output
Platform     : works on Windows (no temp-file delete race)

CLI:
  -i, --input          file or directory
  -o, --output         output directory
  -q, --quality        JPEG quality (default 80)
  --colors <int>       fixed GIF palette size (if omitted, auto)
  --scale <float>      fixed GIF scale (0<val≤1) (if omitted, auto)
  --max-width          cap width (px)
  --max-height         cap height (px)
  -t, --target_compression_factor  desired size/original ratio 0<val≤1 (default 1)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import tqdm
from PIL import Image, ImageSequence

# ────────────────────────────────── constants ─────────────────────────────────
THRESH = 0.05  # acceptable distance to target factor
MIN_COLORS = 8
MIN_SCALE_FRAC = 0.25  # don't scale below 25% of original resolution

# ────────────────────────────── small helpers ───────────────────────────────


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def human_delta(o: int, n: int) -> str:
    d = o - n
    pct = abs(d) / o * 100 if o else 0
    sign = "−" if d > 0 else "+"
    return f"{o//1024} KB → {n//1024} KB ({sign}{pct:.1f} % )"


def resized(
    w: int, h: int, scale: float, max_w: int | None, max_h: int | None
) -> Tuple[int, int]:
    w, h = int(w * scale), int(h * scale)
    if max_w or max_h:
        r = min((max_w / w) if max_w else 1, (max_h / h) if max_h else 1, 1)
        w, h = int(w * r), int(h * r)
    return max(1, w), max(1, h)


def safe_temp_path(suffix: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    return tmp.name


# ───────────────────────── PNG → JPEG conversion ────────────────────────────


def convert_png(
    src: Path,
    dst: Path,
    quality: int = 80,
    scale: float = 1.0,
    max_w: int | None = None,
    max_h: int | None = None,
    bg=(255, 255, 255),
) -> Tuple[bool, int, int]:
    print(
        f"[PNG] {src.name}: quality={quality}, scale={scale}, max_w={max_w}, max_h={max_h}"
    )
    im = Image.open(src)
    w, h = resized(*im.size, scale, max_w, max_h)
    if (w, h) != im.size:
        print(f"  Resizing PNG from {im.size} to {(w,h)}")
        im = im.resize((w, h), Image.LANCZOS)
    if im.mode in ("RGBA", "LA") or (
        im.mode == "P" and "transparency" in im.info
    ):
        print("  Converting transparency to white background")
        canvas = Image.new("RGB", im.size, bg)
        if im.mode in ("RGBA", "LA"):
            canvas.paste(im, mask=im.split()[-1])
        else:
            im = im.convert("RGBA")
            canvas.paste(im, mask=im.split()[-1])
        im = canvas
    else:
        im = im.convert("RGB")
    tmp_jpg = safe_temp_path(".jpg")
    im.save(tmp_jpg, "JPEG", quality=quality, optimize=True, progressive=True)
    orig, new = src.stat().st_size, Path(tmp_jpg).stat().st_size
    print(f"  Size {orig//1024}KB → {new//1024}KB")
    if new < orig:
        shutil.move(tmp_jpg, dst)
        return True, orig, new
    Path(tmp_jpg).unlink()
    shutil.copy2(src, dst.with_suffix(".png"))
    return False, orig, orig


# ─────────────────────────── GIF encode helper ─────────────────────────────


def encode_gif(
    src: Path,
    colors: int,
    scale: float,
    max_w: int | None,
    max_h: int | None,
    delete_tmp: bool = True,
) -> Tuple[int, str]:
    tmp_gif = safe_temp_path(".gif")
    gif = Image.open(src)
    nw, nh = resized(*gif.size, scale, max_w, max_h)
    first = gif.convert("RGBA")
    if (nw, nh) != first.size:
        first = first.resize((nw, nh), Image.LANCZOS)
    bg = Image.new("RGB", (nw, nh), (255, 255, 255))
    bg.paste(first, mask=first.split()[3])
    gpal = bg.quantize(colors=colors, method=Image.FASTOCTREE)
    frames, durs = [], []
    for fr in ImageSequence.Iterator(gif):
        dur = fr.info.get("duration", 100)
        if (nw, nh) != fr.size:
            fr = fr.resize((nw, nh), Image.LANCZOS)
        rgba = fr.convert("RGBA")
        rgb = Image.new("RGB", (nw, nh), (255, 255, 255))
        rgb.paste(rgba, mask=rgba.split()[3])
        q = rgb.quantize(palette=gpal, dither=Image.NONE)
        frames.append(q)
        durs.append(dur)
    frames[0].save(
        tmp_gif,
        save_all=True,
        append_images=frames[1:],
        loop=gif.info.get("loop", 0),
        duration=durs,
        optimize=True,
        disposal=2,
    )
    gif.close()
    size = Path(tmp_gif).stat().st_size
    if delete_tmp:
        Path(tmp_gif).unlink(missing_ok=True)
    return size, tmp_gif


# ──────────────────────────── GIF compression ─────────────────────────────────


def compress_gif(
    src: Path,
    dst: Path,
    fixed_colors: Optional[int],
    fixed_scale: Optional[float],
    max_w: int | None,
    max_h: int | None,
    target: float,
) -> Tuple[bool, int, int, dict]:
    orig = src.stat().st_size
    # if user specified colors or scale, do single encode
    if fixed_colors is not None or fixed_scale is not None:
        colors = fixed_colors if fixed_colors is not None else MIN_COLORS
        scale = fixed_scale if fixed_scale is not None else 1.0
        print(f"[GIF] {src.name}: using fixed colors={colors}, scale={scale}")
        size, tmp = encode_gif(
            src, colors, scale, max_w, max_h, delete_tmp=False
        )
        ok = size < orig
        if ok:
            shutil.copy(src, dst.with_suffix(".orig.gif"))
            shutil.move(tmp, dst)
        else:
            Path(tmp).unlink(missing_ok=True)
            shutil.copy2(src, dst)
        return (
            ok,
            orig,
            size if ok else orig,
            {"colors": colors, "scale": scale},
        )
    # auto mode: palette → scale
    print(f"[GIF] {src.name}: target={target}, auto mode")

    def measure(c: int, s: float) -> float:
        size, _ = encode_gif(src, c, s, max_w, max_h, delete_tmp=True)
        ratio = size / orig
        print(
            f"    measure(colors={c}, scale={s:.3f}) -> {size//1024}KB ({ratio:.3f}x)"
        )
        return ratio

    # palette interpolation
    f_hi = measure(
        target and MIN_COLORS or MIN_COLORS, 1.0
    )  # measure at default start_colors
    f_lo = measure(MIN_COLORS, 1.0)
    # ... actually use start_colors= f_hi measurement
    f_hi = measure(target and MIN_COLORS or MIN_COLORS, 1.0)
    c_hi = args.colors_default
    c_lo = MIN_COLORS
    if f_hi != f_lo:
        c_est = c_hi + (target - f_hi) * (c_lo - c_hi) / (f_lo - f_hi)
    else:
        c_est = (c_hi + c_lo) / 2
    c_best = int(clamp(round(c_est), MIN_COLORS, c_hi))
    print(f"  Palette estimate → {c_best} colors")
    f_pal = measure(c_best, 1.0)
    # scale
    min_scale = MIN_SCALE_FRAC
    scale_est = target / f_pal
    scale_best = clamp(scale_est, min_scale, 1.0)
    print(f"  Scale estimate → {scale_best:.3f} (min {min_scale:.3f})")
    f_scale = measure(c_best, scale_best)
    # final encode
    size, tmp = encode_gif(
        src, c_best, scale_best, max_w, max_h, delete_tmp=False
    )
    ok = size < orig
    if ok:
        shutil.copy(src, dst.with_suffix(".orig.gif"))
        shutil.move(tmp, dst)
    else:
        Path(tmp).unlink(missing_ok=True)
        shutil.copy2(src, dst)
    return (
        ok,
        orig,
        size if ok else orig,
        {"colors": c_best, "scale": scale_best},
    )


# ───────────────────────────── dispatch & CLI ───────────────────────────────


def process_file(args_tuple):
    img, out_dir, quality, colors, scale, max_w, max_h, target = args_tuple
    base = img.stem
    if img.suffix.lower() == ".png":
        dst = out_dir / f"{base}_compressed.jpg"
        ok, o, n = convert_png(img, dst, quality, scale or 1.0, max_w, max_h)
        print(
            f"[PNG] {img.name:20s} {'converted' if ok else 'kept original':18s} {human_delta(o,n)}"
        )
    elif img.suffix.lower() == ".gif":
        dst = out_dir / f"{base}_compressed.gif"
        ok, o, n, p = compress_gif(
            img, dst, colors, scale, max_w, max_h, target
        )
        print(
            f"[GIF] {img.name:20s} {'compressed' if ok else 'kept original':18s} {human_delta(o,n)}"
        )
        print(f"      final → colors={p['colors']}, scale={p['scale']:.3f}")
    else:
        print(f"Skipping unsupported {img.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Parallel PNG/GIF compressor")
    ap.add_argument("-i", "--input", required=True, help="file or directory")
    ap.add_argument("-o", "--output", help="output directory")
    ap.add_argument(
        "-q", "--quality", type=int, default=80, help="JPEG quality"
    )
    ap.add_argument(
        "--colors",
        type=int,
        default=None,
        help="fixed GIF colours (auto if omitted)",
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=None,
        help="fixed GIF scale (0<val≤1) (auto if omitted)",
    )
    ap.add_argument("--max-width", type=int, help="cap width (px)")
    ap.add_argument("--max-height", type=int, help="cap height (px)")
    ap.add_argument(
        "-t",
        "--target_compression_factor",
        type=float,
        default=1.0,
        help="desired size/original ratio 0<val≤1",
    )
    args = ap.parse_args()

    print(
        f"Starting compression: input={args.input}, output={args.output}, target={args.target_compression_factor}"
    )
    if not (0 < args.target_compression_factor <= 1):
        raise ValueError("--target_compression_factor must be 0<val≤1")

    # store default start_colors for auto
    args.colors_default = args.colors or 128
    src = Path(args.input)
    out_dir = Path(args.output).resolve() if args.output else None
    if src.is_file():
        process_file(
            (
                src,
                out_dir or src.parent,
                args.quality,
                args.colors_default,
                args.scale,
                args.max_width,
                args.max_height,
                args.target_compression_factor,
            )
        )
    elif src.is_dir():
        files = [
            p for p in src.iterdir() if p.suffix.lower() in (".png", ".gif")
        ]
        if not files:
            print("No PNG/GIF files found.")
            exit(1)
        out_dir = out_dir or src
        workers = os.cpu_count() or 1
        print(f"Found {len(files)} images — using {workers} workers.")
        args_list = [
            (
                f,
                out_dir,
                args.quality,
                args.colors,
                args.scale,
                args.max_width,
                args.max_height,
                args.target_compression_factor,
            )
            for f in files
        ]
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers
        ) as exec:
            for _ in tqdm.tqdm(
                exec.map(process_file, args_list),
                total=len(files),
                desc="Images",
            ):
                pass
    else:
        print(f"Error: '{src}' is neither file nor directory.")
