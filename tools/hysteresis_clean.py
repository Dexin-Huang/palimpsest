"""
Hysteresis thresholding for document cleaning.

Uses two thresholds:
- High threshold: Identifies definite ink (conservative)
- Low threshold: Extends ink regions if connected to high-threshold pixels

This prevents broken letters while still removing bleed-through.
"""

import cv2
import numpy as np
from PIL import Image
import argparse
from pathlib import Path
from scipy import ndimage


def hysteresis_threshold(gray, low_thresh, high_thresh):
    """
    Apply hysteresis thresholding.

    1. High threshold creates "seed" regions (definite ink)
    2. Low threshold creates "candidate" regions
    3. Only candidates connected to seeds are kept

    This is similar to Canny edge detection's approach.
    """
    # Create binary masks
    high_mask = gray < high_thresh  # Definite ink (dark pixels)
    low_mask = gray < low_thresh    # Candidate ink (includes bleed)

    # Label connected components in the low threshold mask
    labeled, num_features = ndimage.label(low_mask)

    # Find which components contain high-threshold pixels
    # These are the "valid" ink components
    valid_labels = set(np.unique(labeled[high_mask])) - {0}

    # Create output mask with only valid components
    result = np.isin(labeled, list(valid_labels))

    return result.astype(np.uint8) * 255


def adaptive_hysteresis(gray, window_size=25, k_high=0.2, k_low=0.4):
    """
    Adaptive hysteresis using local Sauvola-style thresholds.

    Instead of global thresholds, compute local high/low thresholds
    based on local mean and std.
    """
    # Compute local statistics
    mean = cv2.blur(gray.astype(np.float64), (window_size, window_size))
    mean_sq = cv2.blur(gray.astype(np.float64)**2, (window_size, window_size))
    std = np.sqrt(np.maximum(mean_sq - mean**2, 0))

    R = 128  # Dynamic range

    # High threshold (conservative - definite ink)
    thresh_high = mean * (1 + k_high * (std / R - 1))

    # Low threshold (permissive - includes faint strokes)
    thresh_low = mean * (1 + k_low * (std / R - 1))

    # Create masks
    high_mask = gray < thresh_high  # Definite ink
    low_mask = gray < thresh_low    # Candidate ink

    # Connected component analysis
    labeled, num_features = ndimage.label(low_mask)

    # Find valid components (those containing high-threshold pixels)
    valid_labels = set(np.unique(labeled[high_mask])) - {0}

    # Output mask
    ink_mask = np.isin(labeled, list(valid_labels))

    return ink_mask


def clean_with_hysteresis(image_path, output_path,
                          window_size=25,
                          k_high=0.15,
                          k_low=0.35,
                          keep_grayscale=True,
                          blend=0.2):
    """
    Clean document using adaptive hysteresis thresholding.

    Args:
        k_high: Sauvola k for high threshold (lower = more conservative)
        k_low: Sauvola k for low threshold (higher = more permissive)
    """
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not load: {image_path}")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Get ink mask using hysteresis
    ink_mask = adaptive_hysteresis(gray, window_size, k_high, k_low)

    if keep_grayscale:
        # Create cleaned version
        cleaned = np.full_like(gray, 255)  # White background

        # Keep original gray values where ink is detected
        cleaned[ink_mask] = gray[ink_mask]

        # Optional: slight smoothing at edges
        cleaned = cv2.medianBlur(cleaned, 3)

        # Blend with original for paper texture
        if blend > 0:
            result = cv2.addWeighted(cleaned, 1-blend, gray, blend, 0)
        else:
            result = cleaned

        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    else:
        # Pure binary
        result = np.where(ink_mask, 0, 255).astype(np.uint8)
        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)

    cv2.imwrite(str(output_path), result)
    print(f"Saved: {output_path}")

    # Also save the mask for inspection
    mask_path = str(output_path).replace('.png', '_mask.png')
    cv2.imwrite(mask_path, ink_mask.astype(np.uint8) * 255)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hysteresis thresholding for documents")
    parser.add_argument("input", help="Input image")
    parser.add_argument("-o", "--output", default="hysteresis_clean.png")
    parser.add_argument("--window", "-w", type=int, default=25)
    parser.add_argument("--k-high", type=float, default=0.15,
                        help="High threshold k (conservative, default 0.15)")
    parser.add_argument("--k-low", type=float, default=0.35,
                        help="Low threshold k (permissive, default 0.35)")
    parser.add_argument("--blend", type=float, default=0.15)
    parser.add_argument("--binary", "-b", action="store_true")

    args = parser.parse_args()

    clean_with_hysteresis(
        args.input, args.output,
        window_size=args.window,
        k_high=args.k_high,
        k_low=args.k_low,
        keep_grayscale=not args.binary,
        blend=args.blend
    )
