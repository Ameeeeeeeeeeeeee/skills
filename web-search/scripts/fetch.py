#!/usr/bin/env python3
"""Fetch a URL and extract readable content for the web-search Skill."""

from __future__ import annotations

import argparse
import sys

from search import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CHARS,
    WebSearchError,
    add_network_options,
    clean_text,
    emit,
    env_int,
    run_fetch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--format", choices=("markdown", "text"), default="markdown")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=env_int("WEBSEARCH_MAX_CHARS", DEFAULT_MAX_CHARS),
        help="Maximum extracted characters",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=env_int("WEBSEARCH_MAX_BYTES", DEFAULT_MAX_BYTES),
        help="Maximum response bytes to read",
    )
    add_network_options(parser)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.max_chars < 1 or args.max_bytes < 1:
            raise WebSearchError("--max-chars and --max-bytes must be positive")
        payload = run_fetch(args)
        emit(payload, args.pretty)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must return machine-readable errors.
        emit({"error": clean_text(str(exc)), "operation": "fetch"}, args.pretty)
        return 1


if __name__ == "__main__":
    sys.exit(main())
