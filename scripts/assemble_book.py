import argparse
from pathlib import Path

from palimpsest.book import assemble_book


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble book-level outputs from page JSON")
    parser.add_argument(
        "--transcriptions-dir",
        required=True,
        help="Directory containing *_final.json files",
    )
    args = parser.parse_args()

    index = assemble_book(Path(args.transcriptions_dir))
    print(f"Assembled {index['total_pages']} pages")
    if index.get("missing_final"):
        print(f"Missing final pages: {len(index['missing_final'])}")


if __name__ == "__main__":
    main()
