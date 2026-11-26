"""Usage:
  python -m taste run URL [URL ...] [-f urls.txt] [-o out/]   judge products, in parallel
  python -m taste eval labels.json [-o out/]                  compare verdicts in out/ with labelled calls
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import agent, verify
from .collect import collect, normalize, registrable

ICON = {"trust": "TRUST", "caution": "CAUTION", "avoid": "AVOID"}


def slug(url: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "_", re.sub(r"^https?://(www\.)?", "", normalize(url).lower())).strip("_")


def log(url: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {slug(url)}: {msg}", file=sys.stderr, flush=True)


def judge_one(url: str, out: Path, args) -> dict:
    t0 = time.time()
    try:
        log(url, "collecting signals")
        signals = collect(url)
        (out / f"{slug(url)}.signals.json").write_text(json.dumps(signals, indent=2, ensure_ascii=False))
        log(url, "investigating")
        v = agent.judge(url, signals, args.model, args.effort)
        verify.check_all(v["reasons"])
        bad = verify.failed(v["reasons"])
        if bad and not args.no_repair:
            log(url, f"{len(bad)} of {len(v['reasons'])} sources did not hold up, repairing")
            try:
                v2 = agent.repair(v["_meta"]["session_id"], bad, args.model, args.effort)
                v2["_meta"]["seconds"] += v["_meta"]["seconds"]
                verify.check_all(v2["reasons"])
                v = v2
            except Exception as e:  # keep the first answer rather than lose the product
                log(url, f"repair failed ({e}); keeping first pass")
        # A reason whose source is dead or does not contain the quote must not reach the judges.
        dropped = [r for r in v["reasons"] if r["check"]["status"] in ("dead", "missing")]
        v["reasons"] = [r for r in v["reasons"] if r not in dropped]
        v["dropped_reasons"] = dropped
        v["url"] = normalize(url)
        v["_meta"]["wall_seconds"] = round(time.time() - t0)
        log(url, f"{ICON[v['verdict']]} ({v['confidence']}), {len(v['reasons'])} reasons, "
                 f"{len(dropped)} dropped, {v['_meta']['wall_seconds']}s")
    except Exception as e:
        log(url, f"FAILED: {e}")
        v = {"url": normalize(url), "error": str(e)}
    (out / f"{slug(url)}.json").write_text(json.dumps(v, indent=2, ensure_ascii=False))
    return v


def markdown(verdicts: list[dict]) -> str:
    lines = ["# Verdicts", ""]
    for v in verdicts:
        if "error" in v:
            lines += [f"## {v['url']}", "", f"Run failed: {v['error']}", ""]
            continue
        lines += [f"## {v['product_name']} ({v['vendor_name']}): {ICON[v['verdict']]}", "", v["url"], "",
                  f"Product real and alive: **{v['product_real_and_alive']}**. Company trustworthy: "
                  f"**{v['company_trustworthy']}**. Confidence: {v['confidence']}.", "", v["summary"], ""]
        for r in v["reasons"]:
            sign = {"positive": "+", "negative": "-", "neutral": "~"}[r["direction"]]
            flag = "" if r["check"]["status"] == "ok" else f" _(source check: {r['check']['status']})_"
            lines += [f"- **{sign}** [{r['category']}, {r['weight']}] {r['claim']}  ",
                      f"  [{r['source_url']}]({r['source_url']}): \"{r['quote']}\"{flag}"]
        if v["could_not_check"]:
            lines += ["", "Could not check: " + "; ".join(v["could_not_check"])]
        lines.append("")
    return "\n".join(lines)


def cmd_run(args) -> None:
    urls = list(args.urls)
    if args.file:
        raw = Path(args.file).read_text()
        urls += ([c["url"] for c in json.loads(raw)] if args.file.endswith(".json") else
                 [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")])
    if not urls:
        sys.exit("no URLs given")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.parallel) as ex:
        verdicts = list(ex.map(lambda u: judge_one(u, out, args), urls))
    (out / "verdicts.json").write_text(json.dumps(verdicts, indent=2, ensure_ascii=False))
    (out / "verdicts.md").write_text(markdown(verdicts))
    print(f"{out / 'verdicts.json'}\n{out / 'verdicts.md'}")


def cmd_eval(args) -> None:
    """labels.json: [{"url": ..., "verdict": "trust|caution|avoid"}, ...]. Adapt once the real format is known."""
    labels = {registrable(slug(l["url"]).split("/")[0]): l["verdict"] for l in json.loads(Path(args.labels).read_text())}
    mine = {registrable(slug(v["url"]).split("/")[0]): v for v in json.loads((Path(args.out) / "verdicts.json").read_text())}
    hit = 0
    for dom, want in labels.items():
        got = mine.get(dom, {}).get("verdict", "(not run)")
        hit += got == want
        print(f"{'ok  ' if got == want else 'MISS'} {dom:24} ours={got:10} expected={want}")
    print(f"{hit}/{len(labels)} match")


def main() -> None:
    ap = argparse.ArgumentParser(prog="taste", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("urls", nargs="*")
    r.add_argument("-f", "--file")
    r.add_argument("-o", "--out", default="out")
    r.add_argument("-p", "--parallel", type=int, default=4)
    r.add_argument("--model", default=agent.MODEL)
    r.add_argument("--effort", default=agent.EFFORT)
    r.add_argument("--no-repair", action="store_true")
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("eval")
    e.add_argument("labels")
    e.add_argument("-o", "--out", default="out")
    e.set_defaults(fn=cmd_eval)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
