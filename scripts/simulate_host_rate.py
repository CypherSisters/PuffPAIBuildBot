"""How many projects one job can look at closely, at a given pace on GitHub's file host.

The rule: raw.githubusercontent.com may be read 4 times a
second (``source_fetch.HOST_MIN_INTERVAL_BY_HOST``); every other host stays at
once a second. A project too big for one archive is read file by file on that
host (8 to 14 files, ``where_to_look.start_order``), so its pace decides how
many such projects fit in a job's closer-look time.

This is a simulation, not a live run: it sends nothing. It drives the real
``source_fetch._rate_limit`` with a virtual clock, with the real number of
readers at once (``find_parts.CLOSER_WORKERS``), the real windows
(``find_parts.CLOSER_LOOK_S`` and the latest a look may ask,
``SEARCH_DEADLINE_S - SCORING_S``) and the real last-send margin
(``where_to_look.DEADLINE_GRACE_S``). Each answer is assumed to take
``--answer-s`` seconds. A project counts as looked at closely only when every
one of its files was asked before the last-send time and answered by the end.

    py -3.14 scripts/simulate_host_rate.py
"""
from __future__ import annotations

import argparse
import heapq
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from puffpai import source_fetch  # noqa: E402

RAW_HOST = "raw.githubusercontent.com"


def projects_read(*, window_s: float, files_per_project: int, projects: int, workers: int,
                  grace_s: float, answer_s: float, interval_s: Optional[float] = None,
                  host: str = RAW_HOST) -> int:
    """Projects (each *files_per_project* reads on *host*) fully read inside *window_s*.

    *interval_s* None: the host's real pace from ``source_fetch``'s table.
    """
    saved = dict(source_fetch.HOST_MIN_INTERVAL_BY_HOST), source_fetch.HOST_MIN_INTERVAL_S
    try:
        if interval_s is not None:
            source_fetch.HOST_MIN_INTERVAL_BY_HOST.clear()
            source_fetch.HOST_MIN_INTERVAL_BY_HOST[host] = float(interval_s)
        source_fetch.clear_caches()
        last_send = window_s - grace_s
        queue = list(range(projects))
        # (time this reader is free, reader, project, files left)
        events: list[tuple[float, int, int, int]] = []
        for w in range(min(workers, projects)):
            heapq.heappush(events, (0.0, w, queue.pop(0), files_per_project))
        done = 0
        while events:
            t, w, proj, left = heapq.heappop(events)
            clock_now = [t]

            def _sleep(s: float, _c=clock_now) -> None:
                _c[0] += s

            source_fetch._rate_limit(host, _sleep, lambda _c=clock_now: _c[0])  # noqa: SLF001
            sent = clock_now[0]
            if sent > last_send:
                continue                    # this reader stops: past the last-send time
            answered = sent + answer_s
            left -= 1
            if left == 0:
                if answered <= window_s:
                    done += 1
                if queue:
                    heapq.heappush(events, (answered, w, queue.pop(0), files_per_project))
            else:
                heapq.heappush(events, (answered, w, proj, left))
        return done
    finally:
        source_fetch.HOST_MIN_INTERVAL_BY_HOST.clear()
        source_fetch.HOST_MIN_INTERVAL_BY_HOST.update(saved[0])
        source_fetch.HOST_MIN_INTERVAL_S = saved[1]
        source_fetch.clear_caches()


def table(answer_s: float = 0.3) -> list[dict]:
    from puffpai.catalogs import where_to_look as wtl
    from puffpai.wizard import find_parts as fp

    windows = {"closer-look time (CLOSER_LOOK_S)": fp.CLOSER_LOOK_S,
               "latest a look may ask (SEARCH_DEADLINE_S - SCORING_S)":
                   fp.SEARCH_DEADLINE_S - fp.SCORING_S}
    rows = []
    for label, window in windows.items():
        for files in (8, 11, 14):
            row = {"window": label, "window_s": window, "files_per_project": files}
            for name, interval in (("before (1 per second)", source_fetch.HOST_MIN_INTERVAL_S),
                                   ("now (table)", None)):
                row[name] = projects_read(window_s=window, files_per_project=files,
                                          projects=wtl.READ_PER_JOB, workers=fp.CLOSER_WORKERS,
                                          grace_s=wtl.DEADLINE_GRACE_S, answer_s=answer_s,
                                          interval_s=interval)
            rows.append(row)
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--answer-s", type=float, default=0.3,
                    help="seconds one answer takes (default 0.3)")
    args = ap.parse_args(argv)
    from puffpai.catalogs import where_to_look as wtl
    print(f"pace on {RAW_HOST}: {source_fetch.host_min_interval(RAW_HOST)} s between requests; "
          f"every other host: {source_fetch.HOST_MIN_INTERVAL_S} s")
    print(f"table: {source_fetch.HOST_MIN_INTERVAL_BY_HOST}")
    print(f"projects read file by file, out of {wtl.READ_PER_JOB} per job, answer {args.answer_s} s "
          "(simulation with the real pacing code; nothing sent)")
    for r in table(args.answer_s):
        print(f"  {r['window']} = {r['window_s']:.0f} s, {r['files_per_project']} files each: "
              f"before {r['before (1 per second)']}, now {r['now (table)']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
