"""Library-lifecycle filename and path constants for live doc pipelines."""

from __future__ import annotations

from pathlib import Path


METADATA_FILENAME = "metadata.json"
PAGE_LIST_FILENAME = "page_list.json"
REGISTRY_FILENAME = "index.jsonl"
IMAGES_DIRNAME = "images"
CLEANED_IMAGES_DIRNAME = "images_cleaned"
EXPERIMENTS_DIRNAME = "experiments"
RUNS_DIRNAME = "runs"


def metadata_path(doc_dir: Path) -> Path:
    return Path(doc_dir) / METADATA_FILENAME


def page_list_path(doc_dir: Path) -> Path:
    return Path(doc_dir) / PAGE_LIST_FILENAME


def library_registry_path(library_root: Path) -> Path:
    return Path(library_root) / REGISTRY_FILENAME


def images_dir(doc_dir: Path) -> Path:
    return Path(doc_dir) / IMAGES_DIRNAME


def cleaned_images_dir(doc_dir: Path) -> Path:
    return Path(doc_dir) / CLEANED_IMAGES_DIRNAME


def experiments_dir(doc_dir: Path) -> Path:
    return Path(doc_dir) / EXPERIMENTS_DIRNAME


def runs_dir(doc_dir: Path) -> Path:
    return Path(doc_dir) / RUNS_DIRNAME
