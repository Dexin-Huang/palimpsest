from .discovery import add_subparser as add_discovery_subparser
from .library import add_subparser as add_library_subparser
from .scholar import add_subparser as add_scholar_subparser
from .book import add_subparser as add_book_subparser
from .page import add_subparser as add_page_subparser

__all__ = [
    "add_discovery_subparser",
    "add_library_subparser",
    "add_scholar_subparser",
    "add_book_subparser",
    "add_page_subparser",
]
