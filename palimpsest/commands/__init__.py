from .discovery import add_subparser as add_discovery_subparser
from .library import add_subparser as add_library_subparser
from .transcription import add_subparser as add_transcription_subparser
from .book import add_subparser as add_book_subparser

__all__ = [
    "add_discovery_subparser",
    "add_library_subparser",
    "add_transcription_subparser",
    "add_book_subparser",
]
