#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
images_compress.py
──────────────────
Shrink PNG   → JPEG   (optional resize)
Shrink GIF   → global-palette + resize
Search order : palette → resolution (linear interpolation)
Algorithm    : two-point palette interpolation → proportional scale
Accuracy     : |factor - target| ≤ 0.05
Progress     : tqdm bars (files + frames)
Verbose      : detailed console output
Platform     : works on Windows (no temp-file delete race)

CLI  (abridged)
───────────────
  -i  --input                       file or directory
  -o  --output                      output directory
  -t  --target_compression_factor   0<val≤1   (default 1)
  --colors  <int>                   starting palette size (128)
  --scale   <float>                 starting scale         (1.0 = no resize)
  --max-width / --max-height        absolute caps
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Tuple

import tqdm
from PIL import Image, ImageSequence

# ─────────────────────────────────  constants  ──────────────────────────────
THRESH = 0.05  # acceptable distance to target factor
MIN_COLORS = 8
MIN_SCALE = 0.1

# ──────────────────────────────  small helpers  ─────────────────────────────


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def human_delta(o: int, n: int) -> str:
    d = o - n
    pct = abs(d) / o * 100 if o else 0
    sign = "−" if d > 0 else "+"
    return f"{o//1024} KB → {n//1024} KB ({sign}{pct:.1f} %)"


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


# ──────────────────────────  PNG → JPEG conversion  ──────────────────────────
def convert_png(
    src: Path,
    dst: Path,
    *,
    quality: int = 80,
    scale: float = 1.0,
    max_w: int | None = None,
    max_h: int | None = None,
    bg=(255, 255, 255),
) -> Tuple[bool, int, int]:
    print(
        f"[PNG] Processing {src.name}: quality={quality}, scale={scale}, max_w={max_w}, max_h={max_h}"
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

    orig = src.stat().st_size
    new = Path(tmp_jpg).stat().st_size
    print(f"  PNG size {orig//1024}KB -> {new//1024}KB")
    if new < orig:
        print("  Using JPEG output")
        shutil.move(tmp_jpg, dst)
        return True, orig, new

    print("  No savings, keeping original PNG")
    Path(tmp_jpg).unlink()
    shutil.copy2(src, dst.with_suffix(".png"))
    return False, orig, orig


# ───────────────────────── GIF encode helper ─────────────────────────────────
def encode_gif(
    src: Path,
    *,
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
    gif_iter = ImageSequence.Iterator(gif)
    total = getattr(gif, "n_frames", None) or sum(
        1 for _ in ImageSequence.Iterator(gif)
    )
    it = (
        tqdm.tqdm(
            gif_iter, total=total, leave=False, desc=f"Frames [{src.name}]"
        )
        if delete_tmp
        else gif_iter
    )
    for fr in it:
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


# ─────────────────────────── GIF compression ─────────────────────────────────
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
    orig = src.stat().st_size
    print(
        f"[GIF] Compressing {src.name}: target factor={target}, start_colors={start_colors}, start_scale={start_scale}"
    )

    # measure helper: encode and delete
    def measure(c: int, s: float) -> float:
        size, tmp = encode_gif(
            src, colors=c, scale=s, max_w=max_w, max_h=max_h, delete_tmp=True
        )
        ratio = size / orig
        print(
            f"    measure(colors={c}, scale={s:.3f}) -> {size//1024}KB ({ratio:.3f}x)"
        )
        return ratio

    # palette endpoints
    f_hi = measure(start_colors, start_scale)
    f_lo = measure(MIN_COLORS, start_scale)

    # interpolate color count
    if f_hi != f_lo:
        c_est = start_colors + (target - f_hi) * (
            MIN_COLORS - start_colors
        ) / (f_lo - f_hi)
    else:
        c_est = (start_colors + MIN_COLORS) / 2
    c_best = int(clamp(round(c_est), MIN_COLORS, start_colors))
    print(f"  palette estimate -> c_best={c_best}")
    # avoid re-measuring if same endpoint
    if c_best == start_colors:
        f_pal = f_hi
    elif c_best == MIN_COLORS:
        f_pal = f_lo
    else:
        f_pal = measure(c_best, start_scale)

    # if palette-only suffices
    if abs(f_pal - target) <= THRESH:
        print(f"  palette-only hit: ratio={f_pal:.3f}, within ±{THRESH}")
        size, tmp = encode_gif(
            src,
            colors=c_best,
            scale=start_scale,
            max_w=max_w,
            max_h=max_h,
            delete_tmp=False,
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
            (size if ok else orig),
            {"colors": c_best, "scale": start_scale},
        )

    # scale estimate proportional to size
    scale_est = target / f_pal
    scale_best = clamp(scale_est, MIN_SCALE, start_scale)
    print(f"  scale estimate -> scale_best={scale_best:.3f}")
    f_scale = measure(c_best, scale_best)

    # optional refine
    if abs(f_scale - target) > THRESH:
        scale_ref = clamp(
            scale_best * (target / f_scale), MIN_SCALE, start_scale
        )
        print(f"  refining scale -> scale_ref={scale_ref:.3f}")
        f_ref = measure(c_best, scale_ref)
        if abs(f_ref - target) < abs(f_scale - target):
            scale_best, f_scale = scale_ref, f_ref
            print(
                f"    accepted refined scale -> {scale_best:.3f}, ratio={f_scale:.3f}"
            )

    # final encode and save
    print(
        f"  final choice -> colors={c_best}, scale={scale_best:.3f}, ratio={f_scale:.3f}"
    )
    size, tmp = encode_gif(
        src,
        colors=c_best,
        scale=scale_best,
        max_w=max_w,
        max_h=max_h,
        delete_tmp=False,
    )
    ok = size < orig
    print(
        f"  result size: {size//1024}KB (orig {orig//1024}KB) -> {'compressed' if ok else 'kept original'}"
    )
    if ok:
        shutil.copy(src, dst.with_suffix(".orig.gif"))
        shutil.move(tmp, dst)
    else:
        Path(tmp).unlink(missing_ok=True)
        shutil.copy2(src, dst)

    return (
        ok,
        orig,
        (size if ok else orig),
        {"colors": c_best, "scale": scale_best},
    )


# ───────────────────────────── dispatch & CLI ───────────────────────────────
def process(
    img: Path,
    out_dir: Path | None,
    quality: int,
    colors: int,
    scale: float | None,
    max_w: int | None,
    max_h: int | None,
    target: float,
):
    out_dir = out_dir or img.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    base = img.stem

    if img.suffix.lower() == ".png":
        dst = out_dir / f"{base}_compressed.jpg"
        ok, o, n = convert_png(
            img,
            dst,
            quality=quality,
            scale=scale or 1.0,
            max_w=max_w,
            max_h=max_h,
        )
        print(
            f"[PNG] {img.name:20s} {('converted' if ok else 'kept original'):18s} {human_delta(o,n)}"
        )

    elif img.suffix.lower() == ".gif":
        dst = out_dir / f"{base}_compressed.gif"
        ok, o, n, p = compress_gif(
            img,
            dst,
            start_colors=colors,
            start_scale=scale or 1.0,
            max_w=max_w,
            max_h=max_h,
            target=target,
        )
        print(
            f"[GIF] {img.name:20s} {('compressed' if ok else 'kept original'):18s} {human_delta(o,n)}"
        )
        print(f"      final → colors={p['colors']}, scale={p['scale']:.3f}")

    else:
        print(f"Skipping unsupported {img.name}")


def main():
    ap = argparse.ArgumentParser(
        description="Shrink PNG/GIF (palette + resolution interpolation)"
    )
    ap.add_argument("-i", "--input", required=True, help="file or directory")
    ap.add_argument("-o", "--output", help="output directory")
    ap.add_argument(
        "-q", "--quality", type=int, default=80, help="JPEG quality"
    )
    ap.add_argument(
        "--colors", type=int, default=128, help="start GIF colours"
    )
    ap.add_argument(
        "--scale", type=float, help="start scale (0<val≤1)", default=None
    )
    ap.add_argument("--max-width", type=int, help="cap width  (px)")
    ap.add_argument("--max-height", type=int, help="cap height  (px)")
    ap.add_argument(
        "-t",
        "--target_compression_factor",
        type=float,
        default=1.0,
        help="desired size/original ratio 0<val≤1",
    )
    args = ap.parse_args()

    print(
        f"Starting compression: input={args.input}, output={args.output}, target_factor={args.target_compression_factor}"
    )

    if not (0 < args.target_compression_factor <= 1):
        raise ValueError("--target_compression_factor must be 0<val≤1")

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
    elif src.is_dir():
        files = [
            p for p in src.iterdir() if p.suffix.lower() in (".png", ".gif")
        ]
        if not files:
            print("No PNG/GIF files found.")
            return
        print(f"Found {len(files)} image(s) in directory")
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
    else:
        print(f"Error: '{src}' is neither file nor directory.")


if __name__ == "__main__":
    main()
