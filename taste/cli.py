"""Usage:
  python -m taste run URL [URL ...] [-f urls.txt|cases.json|labels.csv] [-o out/]   judge products, in parallel
  python -m taste report out/                      rebuild report.pdf from the results in a folder
  python -m taste eval labels.csv [-o out/]        compare verdicts in out/ with labelled calls (url + overall)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import agent, browser, verify
from .collect import collect, normalize

COLOURS = ("green", "yellow", "red")


def slug(url: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "_", re.sub(r"^https?://(www\.)?", "", normalize(url).lower())).strip("_")[:120]


LISTENERS: dict = {}  # slug -> callable(msg); the web server follows a run through this


def log(url: str, msg: str) -> None:
    if slug(url) in LISTENERS:
        LISTENERS[slug(url)](msg)
    print(f"[{time.strftime('%H:%M:%S')}] {slug(url)}: {msg}", file=sys.stderr, flush=True)


def judge_one(url: str, out: Path, args) -> dict:
    t0 = time.time()
    try:
        log(url, "collecting signals")
        signals = collect(url)
        (out / f"{slug(url)}.signals.json").write_text(json.dumps(signals, indent=2, ensure_ascii=False))
        shot, full = out / f"{slug(url)}.png", out / f"{slug(url)}.full.png"
        has_shot = browser.screenshot(normalize(url), shot, height=1500)        # the top, at reading size
        shots = [shot] if has_shot else []
        if has_shot and browser.screenshot(normalize(url), full, height=5200):  # the whole page, from further away
            shots.append(full)
        log(url, "investigating" + ("" if has_shot else " (no screenshot)"))
        v = agent.judge(url, signals, shots, args.model, args.effort)
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
        if not args.no_support_check:
            log(url, "checking that each source says what is claimed")
            try:
                unsupported = verify.check_support(v["reasons"], lambda items: agent.support_judge(items, args.model))
                order = {c: i for i, c in enumerate(COLOURS)}
                worst = max((order[r["flag"]] for r in v["reasons"] if r not in unsupported), default=0)
                if unsupported and order[v["verdict"]] > worst and not args.no_repair:
                    # The flag that carried the verdict fell away: let the agent re-source it or revise the call.
                    log(url, "the deciding flag was not supported by its source, revising")
                    v2 = agent.repair(v["_meta"]["session_id"], [
                        {"claim": r["claim"], "source_url": r["source_url"], "quote": r["quote"],
                         "problem": f"the source does not say this: {r['check']['detail']}"} for r in unsupported],
                        args.model, args.effort)
                    v2["_meta"]["seconds"] += v["_meta"]["seconds"]
                    verify.check_all(v2["reasons"])
                    v = v2
            except Exception as e:  # the quote check already passed; do not lose the product over this
                log(url, f"support check failed ({e}); keeping quote-checked reasons")
        for r in v["reasons"]:
            r["check"].pop("context", None)
        # A reason whose source is dead, lacks the quote or does not carry the claim must not reach the judges.
        dropped = [r for r in v["reasons"] if r["check"]["status"] in ("dead", "missing", "unsupported")]
        v["reasons"] = [r for r in v["reasons"] if r not in dropped]
        v["dropped_reasons"] = dropped
        v["url"] = normalize(url)
        v["_meta"] |= {"wall_seconds": round(time.time() - t0), "screenshot": has_shot}
        log(url, f"{v['verdict'].upper()} ({v['confidence']}), {len(v['reasons'])} reasons, "
                 f"{len(dropped)} dropped, {v['_meta']['wall_seconds']}s")
    except Exception as e:
        log(url, f"FAILED: {e}")
        v = {"url": normalize(url), "error": str(e)}
    (out / f"{slug(url)}.json").write_text(json.dumps(v, indent=2, ensure_ascii=False))
    return v


def evidence_link(r: dict) -> str:
    """The source URL with a text fragment, so the browser jumps to the quoted passage and highlights it.

    Uses the quote's first and last few words (browsers match the span between them), which survives
    line breaks and markup inside the passage. Pages that do not support it simply open as usual.
    """
    url, words = r["source_url"], r.get("quote", "").split()
    if "#" in url or len(words) < 4 or r.get("check", {}).get("status") != "ok":
        return url
    enc = lambda ws: urllib.parse.quote(" ".join(ws), safe="").replace("-", "%2D")
    if len(words) <= 8:
        return f"{url}#:~:text={enc(words)}"
    return f"{url}#:~:text={enc(words[:4])},{enc(words[-4:])}"


def flags(v: dict, colour: str) -> list[dict]:
    return [r for r in v.get("reasons", []) if r["flag"] == colour]


def markdown(verdicts: list[dict]) -> str:
    lines = ["# Verdicts", ""]
    for v in verdicts:
        if "error" in v:
            lines += [f"## {v['url']}", "", f"Run failed: {v['error']}", ""]
            continue
        lines += [f"## {v['product_name']}: {v['verdict'].upper()}", "", f"{v['url']}  ",
                  f"{v['product_category']}. Vendor: {v['vendor_name']}  ",
                  f"Lifecycle: {v['lifecycle'].replace('_', ' ')}. Product real and alive: {v['product_real_and_alive']}. "
                  f"Company trustworthy: {v['company_trustworthy']}. Confidence: {v['confidence']}.", "", v["summary"], ""]
        if v["former_names"]:
            lines += ["Former names: " + ", ".join(v["former_names"]), ""]
        for colour in ("red", "yellow", "green"):
            if flags(v, colour):
                lines += [f"**{colour.capitalize()} flags**", ""]
            for r in flags(v, colour):
                note = "" if r["check"]["status"] == "ok" else f" _(source check: {r['check']['status']})_"
                lines += [f"- {r['claim']}  ", f"  [{r['source_url']}]({evidence_link(r)}): \"{r['quote']}\"{note}"]
            if flags(v, colour):
                lines.append("")
        if v["could_not_check"]:
            lines += ["Could not check: " + "; ".join(v["could_not_check"]), ""]
    return "\n".join(lines)


def write_csv(verdicts: list[dict], path: Path) -> None:
    """Same columns as the labelled examples. Each flag carries its quote and a link that opens at the quote."""
    def cell(v, colour):
        return "\n".join(f"- {r['claim']} Source says: \"{r['quote']}\" {evidence_link(r)}"
                         for r in flags(v, colour)) or "None identified."
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "url", "product", "vendor", "overall", "green_flags", "yellow_flags", "red_flags", "reasoning"])
        for i, v in enumerate(verdicts, 1):
            if "error" in v:
                w.writerow([i, v["url"], "", "", "", "", "", "", f"Run failed: {v['error']}"])
            else:
                w.writerow([i, v["url"], f"{v['product_name']} ({v['product_category']})", v["vendor_name"],
                            v["verdict"].capitalize(),
                            cell(v, "green"), cell(v, "yellow"), cell(v, "red"), v["summary"]])


def read_urls(path: str) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    if path.endswith(".json"):
        return [c["url"] for c in json.loads(raw)]
    if path.endswith(".csv"):
        return [r["url"] for r in csv.DictReader(io.StringIO(raw))]
    return [l.strip() for l in raw.splitlines() if l.strip() and not l.startswith("#")]


def cmd_run(args) -> None:
    urls = list(args.urls) + (read_urls(args.file) if args.file else [])
    if args.only:
        urls = [u for u in urls if re.search(args.only, u)]
    if not urls:
        sys.exit("no URLs given")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.parallel) as ex:
        verdicts = list(ex.map(lambda u: judge_one(u, out, args), urls))
    (out / "verdicts.json").write_text(json.dumps(verdicts, indent=2, ensure_ascii=False))
    (out / "verdicts.md").write_text(markdown(verdicts))
    write_csv(verdicts, out / "verdicts.csv")
    from . import report  # imported here: report imports this module
    print(f"{report.build(out)}\n{out / 'verdicts.csv'}\n{out / 'verdicts.json'}\n{out / 'verdicts.md'}")


def cmd_report(args) -> None:
    from . import report
    print(report.build(Path(args.out)))


def cmd_eval(args) -> None:
    """Labels: a CSV with `url` and `overall` (Green/Yellow/Red), or JSON with `url` and `verdict`."""
    raw = Path(args.labels).read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw))) if args.labels.endswith(".csv") else json.loads(raw)
    out = Path(args.out)
    hit = ran = 0
    confusion: dict[tuple, int] = {}
    for row in rows:
        want = (row.get("overall") or row.get("verdict")).lower()
        f = out / f"{slug(row['url'])}.json"
        got = json.loads(f.read_text()).get("verdict", "error") if f.is_file() else "(not run)"
        if got in COLOURS:
            ran += 1
            hit += got == want
            confusion[(want, got)] = confusion.get((want, got), 0) + 1
        print(f"{'ok  ' if got == want else 'MISS'} ours={got:9} theirs={want:7} {row['url'][:70]}")
    print(f"\n{hit}/{ran} match ({len(rows) - ran} not run or failed)")
    print("theirs \\ ours  " + "  ".join(f"{c:>6}" for c in COLOURS))
    for want in COLOURS:
        print(f"{want:13}  " + "  ".join(f"{confusion.get((want, got), 0):>6}" for got in COLOURS))


def main() -> None:
    ap = argparse.ArgumentParser(prog="taste", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("urls", nargs="*")
    r.add_argument("-f", "--file")
    r.add_argument("--only", help="regex: run only the URLs from --file that match")
    r.add_argument("-o", "--out", default="out")
    r.add_argument("-p", "--parallel", type=int, default=4)
    r.add_argument("--model", default=agent.MODEL)
    r.add_argument("--effort", default=agent.EFFORT)
    r.add_argument("--no-repair", action="store_true")
    r.add_argument("--no-support-check", action="store_true")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="rebuild report.pdf from the results in a folder")
    p.add_argument("out")
    p.set_defaults(fn=cmd_report)
    e = sub.add_parser("eval")
    e.add_argument("labels")
    e.add_argument("-o", "--out", default="out")
    e.set_defaults(fn=cmd_eval)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
