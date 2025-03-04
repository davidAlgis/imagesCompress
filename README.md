# Images compression

This Python script compresses PNG images in a specified directory by converting them to JPEG format. It handles transparency by compositing images onto a white background, ensuring the converted images maintain visual quality while potentially reducing file size.

## Installation

Before running the script, install the required Python libraries. You can install them using the provided `requirements.txt` file:

```
pip install -r requirements.txt
```

## Usage

Ensure you have Python 3.6 or newer installed on your system. Clone this repository or download the script and `requirements.txt` file. Install the required libraries as mentioned above. To use the script, run it from the command line with the desired options:

```
python main.py [options]
```

## Options

- `-i`, `--input <directory>`: Specify the input folder containing PNG images to process.
- `-o`, `--output <directory>`: Specify the output folder where the resulting JPEG images will be saved. The folder will be created if it doesn't exist.
- `-q`, `--quality <integer>`: Set the JPEG quality for the conversion (default is 80). Higher values result in better image quality with larger file sizes.
- `-h`, `--help`: Display help information showing all command-line options.

## Example

To convert PNG images from the `source_images` folder and save the JPEG images to the `converted_images` folder with a quality setting of 80, run:

```
python main.py -i source_images -o converted_images -q 80
```
