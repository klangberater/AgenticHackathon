"""Static copy of the web UI with finished results: python -m taste.export RESULTS_DIR SITE_DIR [name ...]

The output is plain files, so it runs on any web host with no server process. With names given, only
those results are published (a name is the result file's stem, e.g. postfix.org).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import cli, server


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, site, only = Path(sys.argv[1]), Path(sys.argv[2]), set(sys.argv[3:])
    (site / "api" / "results").mkdir(parents=True, exist_ok=True)
    for f in server.WEB.iterdir():
        if f.suffix in server.TYPES:
            shutil.copy(f, site / f.name)
    rows = []
    for f in sorted(src.glob("*.json")):
        if f.name.endswith(".signals.json") or (only and f.stem not in only):
            continue
        try:
            v = json.loads(f.read_text())
        except ValueError:
            continue
        if isinstance(v, dict) and v.get("verdict") in cli.COLOURS:
            v.pop("_meta", None)  # session ids stay private
            (site / "api" / "results" / f.stem).write_text(json.dumps(v, ensure_ascii=False))
            rows.append({"file": f.stem, "url": v["url"], "product": v["product_name"], "verdict": v["verdict"]})
    (site / "api" / "recent").write_text(json.dumps(rows, ensure_ascii=False))
    (site / "api" / "config").write_text(json.dumps({"static": True, "token_required": False}))
    print(f"{len(rows)} results exported to {site}")


if __name__ == "__main__":
    main()
