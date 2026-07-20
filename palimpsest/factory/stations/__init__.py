"""Built-in stations. Importing this package registers all of them."""

from palimpsest.factory.stations import (
    align,
    acquire,
    assemble_page,
    deframe,
    dewatermark,
    emend,
    flatten,
    publish,
    read,
    reconstruct,
    reference,
    render_epub,
    segment,
    survey,
    translate,
)

__all__ = [
    "acquire",
    "align",
    "assemble_page",
    "deframe",
    "dewatermark",
    "emend",
    "flatten",
    "publish",
    "read",
    "reconstruct",
    "reference",
    "render_epub",
    "segment",
    "survey",
    "translate",
]
