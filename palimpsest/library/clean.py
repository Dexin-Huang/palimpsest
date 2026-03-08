"""Bleed-through cleaning pipeline for manuscript images.

Triple-blend approach combining:
- Hysteresis thresholding (preserves connected strokes)
- DocRes binarization (deep learning, optional)
- Sauvola thresholding (adaptive local thresholding)

Default weights: 33% Hysteresis + 33% DocRes + 34% Sauvola
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from scipy import ndimage

from .config import PROJECT_ROOT
from .metadata import update_metadata


# ============================================================
# Default parameters (winning combination from Reg.lat.1515)
# ============================================================

DEFAULTS = {
    "k_high": 0.10,
    "k_low": 0.20,
    "hysteresis_window": 25,
    "sauvola_k": 0.30,
    "sauvola_window": 25,
    "sauvola_median": 3,
    "hysteresis_weight": 0.33,
    "sauvola_weight": 0.34,
    "binarization_weight": 0.33,
    "docres_max_dim": 1800,
}


@dataclass
class CleanConfig:
    """Configuration for cleaning pipeline."""

    k_high: float = DEFAULTS["k_high"]
    k_low: float = DEFAULTS["k_low"]
    hysteresis_window: int = DEFAULTS["hysteresis_window"]
    sauvola_k: float = DEFAULTS["sauvola_k"]
    sauvola_window: int = DEFAULTS["sauvola_window"]
    sauvola_median: int = DEFAULTS["sauvola_median"]
    hysteresis_weight: float = DEFAULTS["hysteresis_weight"]
    sauvola_weight: float = DEFAULTS["sauvola_weight"]
    binarization_weight: float = DEFAULTS["binarization_weight"]
    use_docres: bool = True
    docres_max_dim: int = DEFAULTS["docres_max_dim"]


# ============================================================
# Core algorithms
# ============================================================


def hysteresis_threshold(
    gray: np.ndarray, k_high: float, k_low: float, window_size: int
) -> np.ndarray:
    """Adaptive hysteresis thresholding - preserves connected ink strokes.

    Uses two thresholds computed locally:
    - High threshold (k_high): Identifies definite ink (conservative)
    - Low threshold (k_low): Extends ink regions if connected to high-threshold pixels

    Args:
        gray: Grayscale image (uint8)
        k_high: Sauvola k for high threshold (lower = more conservative)
        k_low: Sauvola k for low threshold (higher = more permissive)
        window_size: Local statistics window size

    Returns:
        Binary mask where 255=ink, 0=background
    """
    # Compute local statistics
    mean = cv2.blur(gray.astype(np.float64), (window_size, window_size))
    mean_sq = cv2.blur(gray.astype(np.float64) ** 2, (window_size, window_size))
    std = np.sqrt(np.maximum(mean_sq - mean**2, 0))

    R = 128  # Dynamic range

    # High threshold (conservative - definite ink)
    thresh_high = mean * (1 + k_high * (std / R - 1))

    # Low threshold (permissive - includes faint strokes)
    thresh_low = mean * (1 + k_low * (std / R - 1))

    # Create masks
    high_mask = gray < thresh_high  # Definite ink
    low_mask = gray < thresh_low  # Candidate ink

    # Connected component analysis - keep candidates connected to definite ink
    labeled, _ = ndimage.label(low_mask)
    valid_labels = set(np.unique(labeled[high_mask])) - {0}
    ink_mask = np.isin(labeled, list(valid_labels))

    # Convert to cleaned grayscale image (white background, ink preserved)
    result = np.full_like(gray, 255)
    result[ink_mask] = gray[ink_mask]

    return result


def sauvola_threshold(
    gray: np.ndarray, k: float, window_size: int, median_size: int
) -> np.ndarray:
    """Sauvola adaptive thresholding with median pre-filter.

    T(x,y) = mean(x,y) * [1 + k * (std(x,y)/R - 1)]

    Args:
        gray: Grayscale image (uint8)
        k: Sensitivity parameter (0.2-0.5, higher = more aggressive ink detection)
        window_size: Local window size
        median_size: Median filter kernel size for noise reduction

    Returns:
        Cleaned grayscale image
    """
    R = 128  # Dynamic range

    # Step 1: Median filter to reduce bleed-through noise
    denoised = cv2.medianBlur(gray, median_size)

    # Step 2: Compute local statistics
    mean = cv2.blur(denoised.astype(np.float64), (window_size, window_size))
    mean_sq = cv2.blur(denoised.astype(np.float64) ** 2, (window_size, window_size))
    std = np.sqrt(np.maximum(mean_sq - mean**2, 0))

    # Sauvola threshold
    threshold = mean * (1 + k * (std / R - 1))

    # Apply threshold to create mask
    background_mask = denoised > threshold

    # Create cleaned version - push background toward white
    cleaned = gray.copy()
    cleaned[background_mask] = np.clip(
        gray[background_mask] * 0.3 + 255 * 0.7, 0, 255
    ).astype(np.uint8)

    return cleaned


def blend_images(images: list[np.ndarray], weights: list[float]) -> np.ndarray:
    """Weighted blend of multiple grayscale images.

    Args:
        images: List of grayscale images (same shape)
        weights: List of weights (will be normalized to sum to 1)

    Returns:
        Blended grayscale image
    """
    if len(images) != len(weights):
        raise ValueError("images and weights must have same length")

    # Filter out zero-weight images
    active = [(img, w) for img, w in zip(images, weights) if w > 0]
    if not active:
        raise ValueError("At least one weight must be > 0")

    images, weights = zip(*active)

    # Normalize weights
    total = sum(weights)
    weights = [w / total for w in weights]

    # Blend
    result = np.zeros_like(images[0], dtype=np.float64)
    for img, w in zip(images, weights):
        result += img.astype(np.float64) * w

    return np.clip(result, 0, 255).astype(np.uint8)


# ============================================================
# DocRes integration
# ============================================================

DOCRES_PATH = PROJECT_ROOT / "tools" / "DocRes"
DOCRES_VENV = DOCRES_PATH / ".venv"


def docres_available() -> bool:
    """Check if DocRes is set up with venv and model weights."""
    if not DOCRES_PATH.exists():
        return False

    # Check for venv python
    if sys.platform == "win32":
        python_exe = DOCRES_VENV / "Scripts" / "python.exe"
    else:
        python_exe = DOCRES_VENV / "bin" / "python"

    if not python_exe.exists():
        return False

    # Check for checkpoint (.pkl or .pth)
    checkpoint_dir = DOCRES_PATH / "checkpoints"
    if not checkpoint_dir.exists():
        return False
    checkpoints = list(checkpoint_dir.glob("*.pkl")) + list(checkpoint_dir.glob("*.pth"))
    if not checkpoints:
        return False

    return True


def run_docres_batch(
    input_dir: Path, output_dir: Path, task: str = "binarization", max_dim: int = 1800
) -> Path:
    """Run DocRes binarization via subprocess to its dedicated venv.

    Args:
        input_dir: Directory containing input images
        output_dir: Directory for output images
        task: DocRes task (default: binarization)
        max_dim: Maximum image dimension

    Returns:
        Path to output directory
    """
    if not docres_available():
        raise RuntimeError("DocRes not available - run setup first")

    if sys.platform == "win32":
        python_exe = DOCRES_VENV / "Scripts" / "python.exe"
    else:
        python_exe = DOCRES_VENV / "bin" / "python"

    batch_script = DOCRES_PATH / "batch_process.py"

    result = subprocess.run(
        [
            str(python_exe),
            str(batch_script),
            str(input_dir),
            str(output_dir),
            "--task",
            task,
            "--max-dim",
            str(max_dim),
        ],
        capture_output=True,
        text=True,
        cwd=str(DOCRES_PATH),
    )

    if result.returncode != 0:
        raise RuntimeError(f"DocRes failed: {result.stderr[-500:]}")

    return output_dir


# ============================================================
# Single page and batch processing
# ============================================================


def clean_page(
    image_path: Path,
    output_path: Path,
    config: CleanConfig,
    binarization_path: Optional[Path] = None,
) -> dict:
    """Clean a single page using the triple-blend pipeline.

    Args:
        image_path: Path to input image
        output_path: Path for output image
        config: Cleaning configuration
        binarization_path: Optional path to pre-computed DocRes binarization

    Returns:
        Dict with processing result info
    """
    # Load image
    img = cv2.imread(str(image_path))
    if img is None:
        return {"status": "error", "error": f"Could not load: {image_path}"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Collect cleaned versions and weights
    cleaned_images = []
    weights = []

    # 1. Hysteresis
    if config.hysteresis_weight > 0:
        hysteresis_result = hysteresis_threshold(
            gray, config.k_high, config.k_low, config.hysteresis_window
        )
        cleaned_images.append(hysteresis_result)
        weights.append(config.hysteresis_weight)

    # 2. DocRes binarization (if available)
    if config.binarization_weight > 0 and binarization_path and binarization_path.exists():
        binarization_img = cv2.imread(str(binarization_path), cv2.IMREAD_GRAYSCALE)
        if binarization_img is not None:
            # Resize if needed to match original
            if binarization_img.shape != gray.shape:
                binarization_img = cv2.resize(
                    binarization_img, (gray.shape[1], gray.shape[0])
                )
            cleaned_images.append(binarization_img)
            weights.append(config.binarization_weight)

    # 3. Sauvola
    if config.sauvola_weight > 0:
        sauvola_result = sauvola_threshold(
            gray, config.sauvola_k, config.sauvola_window, config.sauvola_median
        )
        cleaned_images.append(sauvola_result)
        weights.append(config.sauvola_weight)

    if not cleaned_images:
        return {"status": "error", "error": "No cleaning methods enabled"}

    # Blend results
    if len(cleaned_images) == 1:
        blended = cleaned_images[0]
    else:
        blended = blend_images(cleaned_images, weights)

    # Light median blur to smooth transitions
    blended = cv2.medianBlur(blended, 3)

    # Save as color (grayscale in BGR format)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result_bgr = cv2.cvtColor(blended, cv2.COLOR_GRAY2BGR)
    cv2.imwrite(str(output_path), result_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])

    return {
        "status": "ok",
        "input": str(image_path),
        "output": str(output_path),
        "methods": len(cleaned_images),
    }


def clean_document(
    doc_dir: Path,
    output_subdir: str = "images_cleaned",
    pattern: str = "*.jpg",
    workers: int = 4,
    limit: Optional[int] = None,
    overwrite: bool = False,
    config: Optional[CleanConfig] = None,
) -> dict:
    """Clean all pages in a document.

    Args:
        doc_dir: Document directory (containing images/)
        output_subdir: Subdirectory name for cleaned images
        pattern: Glob pattern for input images
        workers: Number of parallel workers for CPU processing
        limit: Maximum images to process
        overwrite: Overwrite existing cleaned images
        config: Cleaning configuration (uses defaults if None)

    Returns:
        Dict with processing summary
    """
    if config is None:
        config = CleanConfig()

    images_dir = doc_dir / "images"
    output_dir = doc_dir / output_subdir

    if not images_dir.exists():
        raise ValueError(f"Images directory not found: {images_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Find images to process
    images = sorted(images_dir.glob(pattern))
    if limit:
        images = images[:limit]

    if not images:
        return {"status": "no_images", "processed": 0}

    # Filter already processed (unless overwrite)
    if not overwrite:
        images = [img for img in images if not (output_dir / img.name).exists()]
        if not images:
            return {"status": "all_skipped", "processed": 0}

    # Build binarization map if using DocRes
    binarization_map: dict[str, Path] = {}
    temp_binarization: Optional[Path] = None

    use_docres = (
        config.use_docres
        and config.binarization_weight > 0
        and docres_available()
    )

    if use_docres:
        print(f"Running DocRes binarization on {len(images)} images...")
        temp_binarization = doc_dir / "temp_binarization"
        temp_binarization.mkdir(parents=True, exist_ok=True)

        # Create temp input dir with just the images we need
        temp_input = doc_dir / "temp_docres_input"
        temp_input.mkdir(parents=True, exist_ok=True)
        for img in images:
            shutil.copy(img, temp_input / img.name)

        try:
            run_docres_batch(
                temp_input, temp_binarization, "binarization", config.docres_max_dim
            )

            # Build mapping - DocRes appends _binarization to filename
            for p in temp_binarization.glob("*_binarization.*"):
                stem = p.stem.replace("_binarization", "")
                binarization_map[stem] = p

        except Exception as e:
            print(f"DocRes failed, continuing without: {e}")
        finally:
            # Cleanup temp input
            if temp_input.exists():
                shutil.rmtree(temp_input, ignore_errors=True)

    # Process pages in parallel
    print(f"Cleaning {len(images)} pages with {workers} workers...")
    results = []
    failed = 0
    processed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                clean_page,
                img,
                output_dir / img.name,
                config,
                binarization_map.get(img.stem),
            ): img
            for img in images
        }

        for future in as_completed(futures):
            img = futures[future]
            try:
                result = future.result()
                results.append(result)
                if result.get("status") == "ok":
                    processed += 1
                else:
                    failed += 1
                    print(f"  Failed: {img.name} - {result.get('error', 'unknown')}")
            except Exception as e:
                failed += 1
                results.append({"status": "error", "error": str(e)})
                print(f"  Error: {img.name} - {e}")

    # Cleanup temp binarization
    if temp_binarization and temp_binarization.exists():
        shutil.rmtree(temp_binarization, ignore_errors=True)

    # Update metadata
    update_metadata(
        doc_dir,
        {
            "cleaning": {
                "output_dir": output_subdir,
                "processed": processed,
                "failed": failed,
                "config": {
                    "k_high": config.k_high,
                    "k_low": config.k_low,
                    "sauvola_k": config.sauvola_k,
                    "hysteresis_weight": config.hysteresis_weight,
                    "sauvola_weight": config.sauvola_weight,
                    "binarization_weight": config.binarization_weight,
                    "used_docres": use_docres,
                },
            }
        },
    )

    return {
        "status": "ok" if failed == 0 else "partial",
        "processed": processed,
        "failed": failed,
        "output_dir": str(output_dir),
    }
