"""Did the agent find the reason that decided the label, not just the right colour?

python -m taste.evalreasons labels.csv OUT_DIR

For every labelled product a tool-less model call reads the reviewer's row and our flags and says
whether the deciding reason is among them. A right colour without the deciding reason is luck. Works on
finished results, so it costs one small call per product and no new investigations.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import agent, cli

SCHEMA = {
    "type": "object",
    "properties": {
        "deciding_reason": {"type": "string", "description": "The one ground that decided the reviewer's call, in one sentence."},
        "found": {"type": "string", "enum": ["yes", "partly", "no"]},
        "matching_flag": {"type": "integer", "description": "Index of our flag that states it, or -1."},
        "note": {"type": "string", "description": "One sentence: what is missing or how ours differs. Empty if found."},
        "beyond_reviewer": {"type": "array", "items": {"type": "integer"},
                            "description": "Indexes of our flags stating a material fact the reviewer's row does not mention."},
    },
    "required": ["deciding_reason", "found", "matching_flag", "note", "beyond_reviewer"], "additionalProperties": False,
}
SYSTEM = """You compare two assessments of the same software product: a human reviewer's and an automated agent's.

First name the one ground that decided the reviewer's overall call. For a red call it is the main red flag; for \
yellow, the main thing a buyer must resolve; for green, the reason the reviewer kept it green despite appearances \
(or, if the row is mainly about a subtlety the agent should surface, that subtlety). Use the reviewer's reasoning \
to choose when several flags are listed.

Then decide whether the agent's flags contain that ground: "yes" if a flag states the same fact, "partly" if a \
flag touches the same matter but misses its substance (for example it mentions an incident but not the regulator's \
finding about it), "no" if nothing does. Judge the facts, not the colour the agent gave them.

Finally list the agent's flags that state a material fact the reviewer's row does not mention at all: a \
regulator action, an incident, an ownership or lifecycle fact, a contradiction. Routine positives (the product \
exists, has docs, has reviews) do not count."""


def judge(row: dict, verdict: dict) -> dict:
    ours = [{"index": i, "flag": r["flag"], "claim": r["claim"]} for i, r in enumerate(verdict["reasons"])]
    text = ("<reviewer>\n" + json.dumps({k: row[k] for k in ("product", "overall", "green_flags", "yellow_flags",
                                                               "red_flags", "reasoning")}, indent=1, ensure_ascii=False)
            + "\n</reviewer>\n<agent>\n" + json.dumps({"verdict": verdict["verdict"], "summary": verdict["summary"],
                                                       "flags": ours}, indent=1, ensure_ascii=False) + "\n</agent>")
    return agent._run(["--system-prompt", SYSTEM, "--no-session-persistence"], [{"type": "text", "text": text}],
                      agent.MODEL, "medium", SCHEMA, tools="")


def main() -> None:
    labels, out = sys.argv[1], Path(sys.argv[2])
    rows = list(csv.DictReader(io.StringIO(Path(labels).read_text(encoding="utf-8-sig"))))
    todo = []
    for row in rows:
        f = out / f"{cli.slug(row['url'])}.json"
        v = json.loads(f.read_text()) if f.is_file() else {}
        if v.get("verdict"):
            todo.append((row, v))
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda rv: judge(*rv), todo))
    tally: dict = collections.Counter()
    saved = []
    for (row, v), res in zip(todo, results):
        same = v["verdict"] == row["overall"].lower()
        tally[(same, res["found"])] += 1
        saved.append({"url": row["url"], "theirs": row["overall"].lower(), "ours": v["verdict"]} | res)
        mark = {"yes": "found ", "partly": "partly", "no": "MISSED"}[res["found"]]
        print(f"{mark} {'call ok  ' if same else 'call diff'} +{len(res['beyond_reviewer'])} new | {row['product'][:34]:34} | {res['deciding_reason'][:110]}")
        if res["found"] != "yes":
            print(f"         {res['note'][:170]}")
    (out / "reasons_eval.json").write_text(json.dumps(saved, indent=1, ensure_ascii=False))
    n = len(todo)
    print(f"\nDeciding reason found: {sum(c for (s, f), c in tally.items() if f == 'yes')}/{n} yes, "
          f"{sum(c for (s, f), c in tally.items() if f == 'partly')} partly, {sum(c for (s, f), c in tally.items() if f == 'no')} no")
    print(f"Right colour and right reason: {tally[(True, 'yes')]}   right colour, reason only partly or not found: "
          f"{tally[(True, 'partly')] + tally[(True, 'no')]}   different colour but reason found: {tally[(False, 'yes')]}")
    print(f"Products where we report a material fact the reviewer's row lacks: {sum(bool(s['beyond_reviewer']) for s in saved)}/{n} "
          f"({sum(len(s['beyond_reviewer']) for s in saved)} flags)")


if __name__ == "__main__":
    main()
