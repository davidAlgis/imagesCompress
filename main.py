import argparse
import os

import tqdm
from PIL import Image, ImageSequence


def convert_png_to_jpeg(input_path, output_path, quality=80):
    """
    Converts a PNG image to JPEG, handling transparency.
    """
    try:
        image = Image.open(input_path)
    except Exception as e:
        raise Exception(f"Unable to open PNG image: {e}")

    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode in ("RGBA", "LA"):
            background.paste(image, mask=image.split()[-1])
        else:
            image = image.convert("RGBA")
            background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    try:
        image.save(output_path, "JPEG", quality=quality, optimize=True)
    except Exception as e:
        raise Exception(f"Unable to save JPEG: {e}")


def compress_gif(input_path, output_path, colors=128):
    """
    Compresses a GIF by reducing colors and optimizing.
    """
    try:
        img = Image.open(input_path)
    except Exception as e:
        raise Exception(f"Unable to open GIF: {e}")

    frames = []
    for frame in ImageSequence.Iterator(img):
        frame = frame.convert("P", palette=Image.ADAPTIVE, colors=colors)
        frames.append(frame)

    try:
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            loop=img.info.get("loop", 0),
            duration=img.info.get("duration", 100),
            optimize=True,
            disposal=2,
        )
    except Exception as e:
        raise Exception(f"Unable to save compressed GIF: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert PNG to JPEG and compress GIFs from a folder."
    )
    parser.add_argument(
        "-i",
        "--input",
        type=str,
        required=True,
        help="Path to the input folder.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Path to the output folder. If omitted, files are saved in the same location with _compressed suffix.",
    )
    parser.add_argument(
        "-q",
        "--quality",
        type=int,
        default=80,
        help="JPEG quality for PNG conversion (default: 80).",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=128,
        help="Color depth for GIF compression (default: 128).",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.input):
        print(
            f"Error: The input folder '{args.input}' does not exist or is not a directory."
        )
        exit(1)

    if args.output:
        if not os.path.exists(args.output):
            os.makedirs(args.output)

    image_files = [
        f
        for f in os.listdir(args.input)
        if os.path.isfile(os.path.join(args.input, f))
        and f.lower().endswith((".png", ".gif"))
    ]

    if not image_files:
        print("No PNG or GIF images found in the specified input folder.")
        exit(0)

    print(f"Found {len(image_files)} image(s) to process.")

    for filename in tqdm.tqdm(image_files, desc="Processing images"):
        input_file = os.path.join(args.input, filename)
        base, ext = os.path.splitext(filename)
        ext = ext.lower()

        if args.output:
            if ext == ".png":
                output_file = os.path.join(
                    args.output, base + "_compressed.jpg"
                )
            elif ext == ".gif":
                output_file = os.path.join(
                    args.output, base + "_compressed.gif"
                )
        else:
            # Save in same folder as input
            if ext == ".png":
                output_file = os.path.join(
                    args.input, base + "_compressed.jpg"
                )
            elif ext == ".gif":
                output_file = os.path.join(
                    args.input, base + "_compressed.gif"
                )

        try:
            if ext == ".png":
                convert_png_to_jpeg(
                    input_file, output_file, quality=args.quality
                )
            elif ext == ".gif":
                compress_gif(input_file, output_file, colors=args.colors)
        except Exception as e:
            print(f"Error processing '{filename}': {e}")

    print("Done processing images.")


if __name__ == "__main__":
    main()
