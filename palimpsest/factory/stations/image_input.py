"""Strict loading for image artifacts consumed by stations."""

from __future__ import annotations

import cv2
import numpy as np

from palimpsest.factory.core.station import Job


def load_image(job: Job, kind: str) -> np.ndarray:
    """Load a declared image input or fail with its artifact path."""
    path = job.path_of(kind)
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Unreadable image: {path}")
    return image
