"""
Recto-Verso bleed-through removal using back-page subtraction.

The bleed on the front (recto) is ink from the back (verso) showing through.
By aligning and subtracting the verso, we can remove the bleed.
"""

import cv2
import numpy as np
from PIL import Image
import argparse
from pathlib import Path


def align_and_subtract(recto_path, verso_path, output_path,
                       blend_factor=0.5,
                       verso_weight=0.7,
                       use_registration=True):
    """
    Remove bleed-through by subtracting aligned verso from recto.

    Args:
        recto_path: Front page image
        verso_path: Back page image
        output_path: Output cleaned image
        blend_factor: How much to blend (0=original, 1=full subtraction)
        verso_weight: Weight of verso in subtraction (tune based on bleed intensity)
        use_registration: Whether to align images (slower but more accurate)
    """

    # Load images
    recto = cv2.imread(str(recto_path))
    verso = cv2.imread(str(verso_path))

    if recto is None or verso is None:
        raise ValueError(f"Could not load images: {recto_path}, {verso_path}")

    print(f"Recto size: {recto.shape}")
    print(f"Verso size: {verso.shape}")

    # Flip verso horizontally (mirror) - bleed is mirrored
    verso_flipped = cv2.flip(verso, 1)

    # Resize verso to match recto if needed
    if verso_flipped.shape != recto.shape:
        verso_flipped = cv2.resize(verso_flipped, (recto.shape[1], recto.shape[0]))

    # Optional: Fine registration using feature matching
    if use_registration:
        print("Aligning images...")
        verso_flipped = register_images(recto, verso_flipped)

    # Convert to float for processing
    recto_f = recto.astype(np.float32) / 255.0
    verso_f = verso_flipped.astype(np.float32) / 255.0

    # The bleed-through appears as darker areas where verso ink shows through
    # We want to "lift" those areas by subtracting the verso contribution

    # Method 1: Simple weighted subtraction
    # Where verso is dark (ink), recto bleed will be dark too
    # Subtracting verso "lifts" those areas

    # Invert verso (so ink becomes bright, paper becomes dark)
    verso_inv = 1.0 - verso_f

    # The bleed is strongest where verso has ink AND recto is not too dark (not recto's own ink)
    # Create a mask for likely bleed areas
    recto_gray = cv2.cvtColor(recto, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    verso_gray = cv2.cvtColor(verso_flipped, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    # Bleed mask: areas where verso has ink (dark) but recto is medium-light (bleed showing)
    verso_ink = verso_gray < 0.5  # Verso ink areas
    recto_medium = (recto_gray > 0.3) & (recto_gray < 0.85)  # Recto bleed areas (not too dark, not white)
    bleed_mask = (verso_ink & recto_medium).astype(np.float32)

    # Smooth the mask
    bleed_mask = cv2.GaussianBlur(bleed_mask, (15, 15), 0)
    bleed_mask = np.stack([bleed_mask] * 3, axis=-1)

    # Apply correction: lighten recto where bleed is detected
    # The amount of lightening is proportional to how dark the verso is there
    correction = verso_inv * verso_weight * bleed_mask

    # Add correction (lighten the bleed areas)
    result = recto_f + correction * blend_factor
    result = np.clip(result, 0, 1)

    # Convert back to uint8
    result = (result * 255).astype(np.uint8)

    # Save
    cv2.imwrite(str(output_path), result)
    print(f"Saved: {output_path}")

    # Also save the bleed mask for inspection
    mask_path = str(output_path).replace('.png', '_bleed_mask.png').replace('.jpg', '_bleed_mask.png')
    cv2.imwrite(mask_path, (bleed_mask[:,:,0] * 255).astype(np.uint8))
    print(f"Saved mask: {mask_path}")

    return result


def register_images(ref, moving):
    """Align moving image to reference using feature matching."""

    # Convert to grayscale
    ref_gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    mov_gray = cv2.cvtColor(moving, cv2.COLOR_BGR2GRAY)

    # Detect ORB features
    orb = cv2.ORB_create(5000)
    kp1, des1 = orb.detectAndCompute(ref_gray, None)
    kp2, des2 = orb.detectAndCompute(mov_gray, None)

    if des1 is None or des2 is None:
        print("Warning: Could not detect features, skipping registration")
        return moving

    # Match features
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    matches = sorted(matches, key=lambda x: x.distance)

    # Use top matches
    good_matches = matches[:min(50, len(matches))]

    if len(good_matches) < 4:
        print("Warning: Not enough matches, skipping registration")
        return moving

    # Get matched keypoints
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

    # Find homography
    M, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, 5.0)

    if M is None:
        print("Warning: Could not compute homography, skipping registration")
        return moving

    # Warp moving image
    aligned = cv2.warpPerspective(moving, M, (ref.shape[1], ref.shape[0]))

    print(f"Aligned with {sum(mask.ravel())} inlier matches")
    return aligned


def main():
    parser = argparse.ArgumentParser(description="Remove bleed-through using recto-verso subtraction")
    parser.add_argument("recto", help="Front page image (with bleed-through)")
    parser.add_argument("verso", help="Back page image (source of bleed)")
    parser.add_argument("-o", "--output", default="cleaned_recto.png", help="Output path")
    parser.add_argument("--blend", type=float, default=0.6, help="Blend factor 0-1 (default 0.6)")
    parser.add_argument("--verso-weight", type=float, default=0.8, help="Verso weight (default 0.8)")
    parser.add_argument("--no-align", action="store_true", help="Skip image alignment")

    args = parser.parse_args()

    align_and_subtract(
        args.recto,
        args.verso,
        args.output,
        blend_factor=args.blend,
        verso_weight=args.verso_weight,
        use_registration=not args.no_align
    )


if __name__ == "__main__":
    main()
