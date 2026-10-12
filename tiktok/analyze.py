"""Finds what's working on your TikTok so the next videos lean into it.

Log each posted video as a row in tiktok/stats.csv (copy the numbers from TikTok Studio > Analytics),
then run:  python tiktok/analyze.py
"""
import csv
import sys
from collections import defaultdict
from statistics import mean

STATS_FILE = sys.argv[1] if len(sys.argv) > 1 else "tiktok/stats.csv"


def engagement_rate(row: dict) -> float:
    views = max(int(row["views"]), 1)
    return (int(row["likes"]) + 2 * int(row["comments"]) + 3 * int(row["shares"])) / views


def length_bucket(seconds: int) -> str:
    if seconds < 30:
        return "<30s"
    if seconds < 60:
        return "30-59s"
    if seconds < 90:
        return "60-89s"
    return "90s+"


def report(rows: list, label: str, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    ranked = sorted(
        groups.items(), key=lambda g: mean(int(r["views"]) for r in g[1]), reverse=True
    )
    print(f"\n== By {label} ==")
    print(f"{label:<22}{'videos':>7}{'avg views':>12}{'eng rate':>10}{'watch %':>9}")
    for name, group in ranked:
        watch = mean(float(r["avg_watch_sec"]) / max(int(r["length_sec"]), 1) for r in group)
        print(
            f"{name:<22}{len(group):>7}{mean(int(r['views']) for r in group):>12,.0f}"
            f"{mean(engagement_rate(r) for r in group):>10.1%}{watch:>9.0%}"
        )
    return ranked[0][0] if ranked else None


def main():
    with open(STATS_FILE, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("views")]
    if not rows:
        print(f"No videos logged in {STATS_FILE} yet.")
        return

    best_sub = report(rows, "subreddit", lambda r: r["subreddit"])
    best_hour = report(rows, "posting hour", lambda r: f"{int(r['posted_hour']):02d}:00")
    best_len = report(rows, "length", lambda r: length_bucket(int(r["length_sec"])))

    top = sorted(rows, key=lambda r: int(r["views"]), reverse=True)[:3]
    print("\n== Top videos ==")
    for r in top:
        print(f"{int(r['views']):>10,}  {r['title']}")

    print("\n== Do more of this ==")
    print(f"- Pull from r/{best_sub} (set reddit.thread.subreddit in config.toml)")
    print(f"- Post around {best_hour}")
    print(f"- Aim for {best_len} videos (tune reddit.thread.max_comment_length / comment count)")
    if len(rows) < 10:
        print(f"- Only {len(rows)} videos logged: treat this as a hint, not a rule, until you have 10+")


if __name__ == "__main__":
    main()
