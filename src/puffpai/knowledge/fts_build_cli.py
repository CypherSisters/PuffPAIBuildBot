"""
PuffPAI Full-Text Index Builder CLI

Builds the SQLite FTS5 shards that back PuffPAI's own offline full-text
knowledge search. One shard per ZIM file; the build is resumable, so an
interrupted run can simply be re-issued.

Usage:
    python -m puffpai.knowledge.fts_build_cli
    python -m puffpai.knowledge.fts_build_cli --zim-dir /data/zim --fts-dir /data/fts
    python -m puffpai.knowledge.fts_build_cli --only wikipedia_en_all_maxi_2024_01
    python -m puffpai.knowledge.fts_build_cli --force

Exit codes:
    0  every ZIM was built or skipped (individual failures are reported)
    1  nothing to do or nowhere to write (no ZIMs found / fts_dir unwritable)

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .discovery import discover_zim_files
from .fts_index import BuildSummary, FTSBuilder, library_id_for, resolve_fts_dir

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m puffpai.knowledge.fts_build_cli",
        description="Build SQLite FTS5 full-text shards over ZIM knowledge packs.",
    )
    parser.add_argument(
        '--zim-dir', action='append', default=None, metavar='DIR',
        help="Directory to scan for .zim files (repeatable; "
             "defaults to config paths.zim_dirs)")
    parser.add_argument(
        '--fts-dir', default=None, metavar='DIR',
        help="Directory to write shard databases into "
             "(defaults to config paths.fts_dir or a sibling 'fts' dir)")
    parser.add_argument(
        '--only', action='append', default=None, metavar='LIBRARY_ID',
        help="Build only this library id (repeatable)")
    parser.add_argument(
        '--force', action='store_true',
        help="Rebuild shards from scratch even if they are already complete")
    parser.add_argument(
        '--batch-size', type=int, default=500, metavar='N',
        help="Entries per commit / progress line (default 500)")
    return parser


def _discover(zim_dirs: list[str] | None) -> list[str]:
    """Discover ZIM files from explicit dirs, or from config when none given."""
    if zim_dirs:
        return discover_zim_files(zim_dirs=zim_dirs)
    try:
        from puffpai.config import get_config
        cfg = get_config()
        return discover_zim_files(zim_dirs=cfg.paths.zim_dirs,
                                  zim_files=cfg.paths.zim_files)
    except Exception as exc:
        print(f"Could not load config for ZIM discovery: {exc}", file=sys.stderr)
        return []


def _resolve_fts_dir(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    try:
        from puffpai.config import get_config
        return resolve_fts_dir(get_config())
    except Exception as exc:
        print(f"Could not resolve FTS directory from config: {exc}",
              file=sys.stderr)
        return ''


def _writable(fts_dir: str) -> bool:
    try:
        os.makedirs(fts_dir, exist_ok=True)
    except OSError as exc:
        print(f"FTS directory is not creatable: {fts_dir} ({exc})",
              file=sys.stderr)
        return False
    if not os.access(fts_dir, os.W_OK):
        print(f"FTS directory is not writable: {fts_dir}", file=sys.stderr)
        return False
    return True


def _progress_printer(lib_id: str):
    def _cb(done: int, total: int, indexed: int) -> None:
        print(f"[{lib_id}] {done:,}/{total:,} entries, "
              f"{indexed:,} articles indexed", flush=True)
    return _cb


def _report(summary: BuildSummary, elapsed: float) -> None:
    if summary.status == 'skipped':
        print(f"  = {summary.library_id}: already complete "
              f"({summary.articles_indexed:,} articles), skipped")
    elif summary.status == 'error':
        print(f"  ! {summary.library_id}: ERROR - {summary.error}")
    else:
        resumed = (f", resumed at entry {summary.resumed_from:,}"
                   if summary.resumed_from else "")
        print(f"  + {summary.library_id}: {summary.articles_indexed:,} articles "
              f"from {summary.total_entries:,} entries in {elapsed:.1f}s{resumed}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)

    fts_dir = _resolve_fts_dir(args.fts_dir)
    if not fts_dir or not _writable(fts_dir):
        return 1

    zim_paths = _discover(args.zim_dir)
    if args.only:
        wanted = set(args.only)
        zim_paths = [p for p in zim_paths if library_id_for(p) in wanted]

    if not zim_paths:
        print("No ZIM files found to index.", file=sys.stderr)
        return 1

    print(f"FTS directory: {fts_dir}")
    print(f"ZIM files    : {len(zim_paths)}")
    print()

    builder = FTSBuilder()
    summaries: list[BuildSummary] = []

    for zim_path in zim_paths:
        lib_id = library_id_for(zim_path)
        print(f"Indexing {os.path.basename(zim_path)} -> {lib_id}.db")
        started = time.monotonic()
        summary = builder.build(
            zim_path, fts_dir,
            batch_size=max(1, args.batch_size),
            progress_cb=_progress_printer(lib_id),
            force=args.force,
        )
        summaries.append(summary)
        _report(summary, time.monotonic() - started)
        print()

    built = [s for s in summaries if s.status == 'complete']
    skipped = [s for s in summaries if s.status == 'skipped']
    failed = [s for s in summaries if s.status == 'error']
    total_articles = sum(s.articles_indexed for s in summaries)

    # Output stays strictly ASCII: this CLI is normally run with stdout
    # redirected to a log file, and on a non-UTF-8 locale a single box-drawing
    # character raises UnicodeEncodeError and takes the whole run down with it.
    print("-" * 60)
    print(f"Built {len(built)}, skipped {len(skipped)}, failed {len(failed)} "
          f"of {len(summaries)} ZIM files")
    print(f"Total articles indexed: {total_articles:,}")
    if failed:
        print("Failed libraries:")
        for s in failed:
            print(f"  - {s.library_id}: {s.error}")

    # Per-ZIM failures are expected on a shelf of downloaded packs (a truncated
    # download, a removable drive that went away) and must not fail the run.
    return 0


if __name__ == '__main__':
    sys.exit(main())
