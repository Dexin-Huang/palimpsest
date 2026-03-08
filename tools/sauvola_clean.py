"""
Sauvola adaptive thresholding + Median filter for bleed-through removal.
Lightweight SOTA approach - gets 90% of the way in seconds.
"""

import cv2
import numpy as np
from PIL import Image
import argparse
from pathlib import Path


def sauvola_threshold(gray, window_size=25, k=0.2, R=128):
    """
    Sauvola adaptive thresholding.

    Unlike global Otsu, Sauvola calculates threshold locally based on
    local mean and standard deviation. This handles uneven illumination
    and distinguishes faint bleed from actual ink.

    T(x,y) = mean(x,y) * [1 + k * (std(x,y)/R - 1)]

    Args:
        gray: Grayscale image
        window_size: Local window size (odd number)
        k: Sensitivity parameter (0.2-0.5, higher = more aggressive)
        R: Dynamic range of standard deviation (usually 128)
    """
    # Compute local mean using box filter
    mean = cv2.blur(gray.astype(np.float64), (window_size, window_size))

    # Compute local standard deviation
    mean_sq = cv2.blur(gray.astype(np.float64)**2, (window_size, window_size))
    std = np.sqrt(np.maximum(mean_sq - mean**2, 0))

    # Sauvola threshold
    threshold = mean * (1 + k * (std / R - 1))

    # Apply threshold
    binary = (gray > threshold).astype(np.uint8) * 255

    return binary


def clean_bleedthrough(image_path, output_path,
                       window_size=25,
                       k=0.3,
                       median_size=3,
                       keep_grayscale=True,
                       blend_with_original=0.3):
    """
    Clean bleed-through using Sauvola + Median filter.

    Args:
        image_path: Input image
        output_path: Output path
        window_size: Sauvola window size (larger = smoother threshold)
        k: Sauvola sensitivity (higher = more aggressive ink detection)
        median_size: Median filter kernel size (removes salt/pepper noise)
        keep_grayscale: If True, outputs grayscale; if False, binary
        blend_with_original: Blend cleaned with original (0=full clean, 1=original)
    """
    # Load image
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not load: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Step 1: Median filter to reduce bleed-through noise
    # Bleed-through is typically lower frequency than text
    denoised = cv2.medianBlur(gray, median_size)

    # Step 2: Sauvola thresholding
    # This creates a mask of what's "ink" vs "background/bleed"
    binary = sauvola_threshold(denoised, window_size, k)

    if keep_grayscale:
        # Instead of pure binary, use the mask to clean the original
        # Keep ink dark, push bleed toward white

        # Create a "cleaned" version where bleed areas become white
        cleaned = gray.copy()

        # Where binary says "background" (white), lighten the original
        background_mask = binary == 255

        # For background areas, push toward white but keep some texture
        cleaned[background_mask] = np.clip(
            gray[background_mask] * 0.3 + 255 * 0.7,
            0, 255
        ).astype(np.uint8)

        # Apply light median to smooth transitions
        cleaned = cv2.medianBlur(cleaned, 3)

        # Optional: blend with original to retain some paper texture
        if blend_with_original > 0:
            result = cv2.addWeighted(
                cleaned, 1 - blend_with_original,
                gray, blend_with_original,
                0
            )
        else:
            result = cleaned

        # Convert back to BGR for saving
        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        # Pure binary output
        result = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

    # Save
    cv2.imwrite(str(output_path), result)
    print(f"Saved: {output_path}")

    return result


def batch_clean(input_folder, output_folder, **kwargs):
    """Clean all images in a folder."""
    input_path = Path(input_folder)
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    extensions = {'.jpg', '.jpeg', '.png'}
    images = sorted([f for f in input_path.iterdir() if f.suffix.lower() in extensions])

    print(f"Processing {len(images)} images...")

    for i, img_path in enumerate(images, 1):
        out_path = output_path / f"{img_path.stem}_sauvola.png"
        try:
            clean_bleedthrough(img_path, out_path, **kwargs)
            print(f"[{i}/{len(images)}] {img_path.name}")
        except Exception as e:
            print(f"[{i}/{len(images)}] FAILED {img_path.name}: {e}")

    print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sauvola + Median bleed-through removal")
    parser.add_argument("input", help="Input image or folder")
    parser.add_argument("-o", "--output", help="Output path or folder")
    parser.add_argument("--window", "-w", type=int, default=25,
                        help="Sauvola window size (default: 25)")
    parser.add_argument("--k", type=float, default=0.3,
                        help="Sauvola sensitivity (default: 0.3, range 0.2-0.5)")
    parser.add_argument("--median", "-m", type=int, default=3,
                        help="Median filter size (default: 3)")
    parser.add_argument("--binary", "-b", action="store_true",
                        help="Output pure binary instead of grayscale")
    parser.add_argument("--blend", type=float, default=0.2,
                        help="Blend with original (default: 0.2)")

    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_file():
        output = args.output or f"{input_path.stem}_sauvola.png"
        clean_bleedthrough(
            input_path, output,
            window_size=args.window,
            k=args.k,
            median_size=args.median,
            keep_grayscale=not args.binary,
            blend_with_original=args.blend
        )
    else:
        output = args.output or f"{input_path}_sauvola"
        batch_clean(
            input_path, output,
            window_size=args.window,
            k=args.k,
            median_size=args.median,
            keep_grayscale=not args.binary,
            blend_with_original=args.blend
        )
