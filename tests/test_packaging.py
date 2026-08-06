from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "palimpsest" / "factory"
RESOURCE_TREES = {
    "recipes": frozenset({".yaml"}),
    "prompts": frozenset({".txt"}),
    "candidates": frozenset({".yaml"}),
    "judges": frozenset({".yaml"}),
    "evaluation/suites": frozenset({".yaml"}),
    "evaluation/cases": frozenset({".json", ".jsonl", ".jpg", ".pgm"}),
    "evaluation/gold": frozenset({".json", ".jpg", ".epub"}),
    "publication_contract": frozenset({".json"}),
}


def _package_path(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def test_wheel_contains_and_resolves_factory_runtime_resources(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    for filename in ("pyproject.toml", "README.md"):
        shutil.copy2(PROJECT_ROOT / filename, source_root / filename)
    shutil.copytree(
        PROJECT_ROOT / "palimpsest",
        source_root / "palimpsest",
        ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]"),
    )
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("palimpsest_cli-*.whl"))

    expected_resources = {
        _package_path(path)
        for relative_root, suffixes in RESOURCE_TREES.items()
        for path in (PACKAGE_ROOT / relative_root).rglob("*")
        if path.is_file() and path.suffix in suffixes
    }
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
        recipe_documents = {
            name: yaml.safe_load(archive.read(name))
            for name in members
            if name.startswith("palimpsest/factory/recipes/") and name.endswith(".yaml")
        }

    packaged_data = {
        name
        for name in members
        if name.startswith("palimpsest/") and not name.endswith(".py")
    }
    assert packaged_data == expected_resources
    assert {
        "palimpsest/factory/recipes/latin_manuscript.yaml",
        "palimpsest/factory/recipes/chinese_scroll_rig.yaml",
        "palimpsest/factory/prompts/read/la/diplomatic.txt",
        "palimpsest/factory/prompts/read/zh/diplomatic.txt",
        "palimpsest/factory/candidates/read/zh-current-production-moving-v1.yaml",
        "palimpsest/factory/judges/read-image-pairwise-qwen3.8-v1.yaml",
        "palimpsest/factory/evaluation/suites/read/zh-vatican-borg-cin-361-f004r-development-v1.yaml",
        "palimpsest/factory/evaluation/cases/emend/assets/p001_clean.jpg",
        "palimpsest/factory/evaluation/gold/render_epub/expected-book-v2.epub",
    } <= members
    for recipe in recipe_documents.values():
        for step in recipe["line"]:
            prompt_name = step.get("prompt")
            if prompt_name is not None:
                assert f"palimpsest/factory/prompts/{prompt_name}.txt" in members
    assert not any(
        name.startswith(("library/", ".env"))
        or "/__pycache__/" in name
        or "/evaluations/runs/" in name
        for name in members
    )

    source_files = {
        path.relative_to(source_root).as_posix()
        for path in (source_root / "palimpsest").rglob("*")
        if path.is_file()
    }
    stray = {
        name for name in members if name.startswith("palimpsest/") and name not in source_files
    }
    assert not stray, (
        "wheel ships files absent from the source tree "
        f"(stale build/ leak?): {sorted(stray)[:5]}"
    )

    install_root = tmp_path / "installed"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--target",
            str(install_root),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    smoke = f"""
import json
import sys
from importlib.metadata import distribution

sys.path.insert(0, {str(install_root)!r})
from palimpsest.factory import prompt_store
from palimpsest.factory.core.recipe import load
from palimpsest.factory.publication_contract import schema_paths

recipes = [
    load(name)
    for name in ("latin_manuscript", "chinese_scroll_rig")
]
prompts = {{
    spec.prompt_name: prompt_store.load(spec.prompt_name)
    for recipe in recipes
    for spec in recipe.steps
    if spec.prompt_name is not None
}}
schemas = sorted(path.name for path in schema_paths().values())
dist = distribution("palimpsest-cli")
print(json.dumps({{
    "module": str(sys.modules["palimpsest"].__file__),
    "recipes": [recipe.name for recipe in recipes],
    "prompts": sorted(prompts),
    "name": dist.metadata["Name"],
    "schemas": schemas,
    "version": dist.version,
}}))
"""
    completed = subprocess.run(
        [sys.executable, "-P", "-c", smoke],
        cwd=outside_checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    proof = json.loads(completed.stdout)
    assert Path(proof["module"]).is_relative_to(install_root)
    assert proof["recipes"] == [
        "latin_manuscript",
        "chinese_scroll_rig",
    ]
    assert {"read/la/diplomatic", "transcribe/zh/foreman_v12"} <= set(proof["prompts"])
    assert proof["schemas"] == [
        "book-object-v1.schema.json",
        "library-object-v1.schema.json",
    ]
    assert proof["name"] == "palimpsest-cli"
    assert proof["version"] == "0.2.0"


def test_checkout_has_no_stale_build_tree() -> None:
    # setuptools build_py copies into build/lib but never removes files that
    # vanished from the source; a stale build/ silently ships retired modules
    # (e.g. gateway/gemini.py) in every wheel built from this checkout.
    assert not (PROJECT_ROOT / "build").exists(), (
        "stale build/ tree would leak deleted modules into the wheel; delete it"
    )
