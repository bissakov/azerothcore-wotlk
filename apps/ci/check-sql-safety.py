#!/usr/bin/env python3

import argparse
from pathlib import Path, PurePosixPath
import sys

from sqlfluff.core import Linter
from sqlfluff.core.config import FluffConfig

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SQL_PARSER = Linter(config=FluffConfig.from_path(str(REPOSITORY_ROOT)))


def is_historical_core_sql(path: PurePosixPath) -> bool:
    parts = path.parts
    if parts[:3] in (("data", "sql", "base"), ("data", "sql", "archive")):
        return True

    return len(parts) >= 4 and parts[:3] == ("data", "sql", "updates") and parts[3].startswith("db_")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check changed SQL for repository safety rules.")
    parser.add_argument(
        "--allow-historical",
        action="store_true",
        help="Allow an explicitly approved historical core SQL change.",
    )
    parser.add_argument("files", nargs="+", help="Changed SQL paths, including deleted paths.")
    return parser.parse_args()


def engine_changes(parsed_file) -> list[tuple[int, str]]:
    if parsed_file.tree is None:
        return []

    changes: list[tuple[int, str]] = []
    for statement_type in ("create_table_statement", "alter_table_statement"):
        for statement in parsed_file.tree.recursive_crawl(statement_type):
            code_segments = [segment for segment in statement.raw_segments if segment.is_code]
            for index, segment in enumerate(code_segments):
                if segment.get_type() != "parameter" or segment.raw_upper != "ENGINE":
                    continue

                value_index = index + 1
                if value_index < len(code_segments) and code_segments[value_index].raw == "=":
                    value_index += 1
                if value_index >= len(code_segments):
                    continue

                value = code_segments[value_index]
                engine = value.raw.strip("`")
                if engine.casefold() != "innodb":
                    line = value.pos_marker.line_no if value.pos_marker else 1
                    changes.append((line, engine))

    return changes


def main() -> int:
    args = parse_args()
    errors: list[str] = []

    for raw_path in args.files:
        relative_path = PurePosixPath(raw_path.replace("\\", "/"))
        display_path = relative_path.as_posix()

        if not args.allow_historical and is_historical_core_sql(relative_path):
            errors.append(f"{display_path}: historical core SQL is immutable; add a pending migration instead")

        path = Path(raw_path)
        if not path.is_file():
            continue

        try:
            contents = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{display_path}: file is not valid UTF-8")
            continue

        parsed_file = SQL_PARSER.parse_string(contents, fname=display_path)
        for line, engine in engine_changes(parsed_file):
            errors.append(f"{display_path}:{line}: new or changed tables must use InnoDB, not {engine}")

    if errors:
        print("SQL safety checks failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
