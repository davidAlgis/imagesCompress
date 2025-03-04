import argparse
import os
from PIL import Image
import tqdm


def convert_png_to_jpeg(input_path, output_path, quality=80):
    """
    Opens a PNG image, converts it to JPEG, and saves it with the given quality.
    If the image has transparency, it composites it onto a white background.
    """
    try:
        image = Image.open(input_path)
    except Exception as e:
        raise Exception(f"Unable to open image: {e}")

    # Check for transparency (alpha channel) and composite on white background if needed.
    if image.mode in ("RGBA", "LA") or (image.mode == "P"
                                        and 'transparency' in image.info):
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


def main():
    parser = argparse.ArgumentParser(
        description="Compress PNG images in a folder to JPEG format.")
    parser.add_argument('-i',
                        '--input',
                        type=str,
                        required=True,
                        help='Path to the input folder containing PNG images.')
    parser.add_argument(
        '-o',
        '--output',
        type=str,
        required=True,
        help='Path to the output folder where JPEG images will be saved.')
    parser.add_argument('-q',
                        '--quality',
                        type=int,
                        default=80,
                        help='JPEG quality (default: 80).')

    args = parser.parse_args()

    # Validate input folder.
    if not os.path.isdir(args.input):
        print(
            f"Error: The input folder '{args.input}' does not exist or is not a directory."
        )
        exit(1)

    # Create output folder if it doesn't exist.
    if not os.path.exists(args.output):
        os.makedirs(args.output)

    # List all PNG files in the input folder.
    png_files = [
        f for f in os.listdir(args.input)
        if os.path.isfile(os.path.join(args.input, f))
        and f.lower().endswith('.png')
    ]

    if not png_files:
        print("No PNG images found in the specified input folder.")
        exit(0)

    print(f"Found {len(png_files)} PNG image(s) to process.")

    for filename in tqdm.tqdm(png_files, desc="Processing images"):
        input_file = os.path.join(args.input, filename)
        base, _ = os.path.splitext(filename)
        output_file = os.path.join(args.output, base + '.jpg')
        try:
            convert_png_to_jpeg(input_file, output_file, quality=args.quality)
        except Exception as e:
            print(f"Error processing '{filename}': {e}")

    print("Done converting images.")


if __name__ == "__main__":
    main()
