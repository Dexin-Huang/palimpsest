from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from palimpsest.config import (
    DEFAULT_EDITION_FONT_CJK,
    DEFAULT_EDITION_FONT_LATIN,
    PROJECT_ROOT,
)


@dataclass(frozen=True)
class FontChoice:
    mode: str
    value: str


@dataclass(frozen=True)
class EditionFontPolicy:
    latin: FontChoice
    cjk: FontChoice

    def latex_lines(self) -> list[str]:
        return [
            _latex_font_line("main", self.latin),
            _latex_font_line("cjk", self.cjk),
        ]

    def as_dict(self) -> dict[str, dict[str, str]]:
        return {
            "latin": {"mode": self.latin.mode, "value": self.latin.value},
            "cjk": {"mode": self.cjk.mode, "value": self.cjk.value},
        }


def _path_value(path: Path) -> str:
    return path.resolve().as_posix()


def _font_line_from_path(command: str, font_path: Path) -> str:
    return rf"\{command}{{{font_path.name}}}[Path={{{_path_value(font_path.parent)}/}}]"


def _latex_font_line(kind: str, choice: FontChoice) -> str:
    if kind == "main":
        command = "setmainfont"
        fallback = r"\IfFontExistsTF{Junicode}{\setmainfont{Junicode}}{\setmainfont{Times New Roman}}"
    else:
        command = "setCJKmainfont"
        fallback = (
            r"\IfFontExistsTF{Noto Serif CJK SC}{\setCJKmainfont{Noto Serif CJK SC}}{%"
            "\n"
            r"  \IfFontExistsTF{Source Han Serif SC}{\setCJKmainfont{Source Han Serif SC}}{\setCJKmainfont{SimSun}}%"
            "\n"
            r"}"
        )

    if choice.mode in {"env_path", "bundled"}:
        return _font_line_from_path(command, Path(choice.value))
    if choice.mode == "env_family":
        return rf"\{command}{{{choice.value}}}"
    return fallback


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path.resolve()
    return None


def _bundled_latin_font() -> Path | None:
    return _first_existing(
        [
            PROJECT_ROOT / "fonts" / "latin" / "Junicode-Regular.ttf",
            PROJECT_ROOT / "fonts" / "latin" / "Junicode.ttf",
            PROJECT_ROOT / "fonts" / "latin" / "NotoSerif-Regular.ttf",
            PROJECT_ROOT / "fonts" / "latin" / "EBGaramond-Regular.ttf",
        ]
    )


def _bundled_cjk_font() -> Path | None:
    return _first_existing(
        [
            PROJECT_ROOT / "fonts" / "cjk" / "NotoSerifCJKsc-Regular.otf",
            PROJECT_ROOT / "fonts" / "cjk" / "NotoSerifSC-Regular.otf",
            PROJECT_ROOT / "fonts" / "cjk" / "SourceHanSerifSC-Regular.otf",
            PROJECT_ROOT / "fonts" / "cjk" / "NotoSansCJKsc-Regular.otf",
        ]
    )


def _resolve_choice(env_value: str, bundled: Path | None, fallback: str) -> FontChoice:
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.exists():
            return FontChoice(mode="env_path", value=str(env_path.resolve()))
        return FontChoice(mode="env_family", value=env_value)
    if bundled is not None:
        return FontChoice(mode="bundled", value=str(bundled))
    return FontChoice(mode="system_fallback", value=fallback)


def resolve_edition_font_policy() -> EditionFontPolicy:
    latin = _resolve_choice(
        DEFAULT_EDITION_FONT_LATIN,
        _bundled_latin_font(),
        "Junicode -> Times New Roman",
    )
    cjk = _resolve_choice(
        DEFAULT_EDITION_FONT_CJK,
        _bundled_cjk_font(),
        "Noto Serif CJK SC -> Source Han Serif SC -> SimSun",
    )
    return EditionFontPolicy(latin=latin, cjk=cjk)
