"""Local web UI: python -m taste.server [port]. Binds to localhost only."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from . import agent, cli
from .collect import normalize

WEB = Path(__file__).resolve().parent.parent / "web"
OUT = Path("out/web")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
JOBS: dict[str, dict] = {}
ARGS = SimpleNamespace(model=agent.MODEL, effort=agent.EFFORT, no_repair=False)
MAX_RUNNING = 4


def run(job: dict) -> None:
    key = cli.slug(job["url"])
    cli.LISTENERS[key] = lambda msg: job["steps"].append({"at": round(time.time() - job["started"]), "text": msg})
    try:
        job["result"] = cli.judge_one(job["url"], OUT, ARGS)
    finally:
        cli.LISTENERS.pop(key, None)
        job["done"] = True


def recent() -> list[dict]:
    rows = []
    for f in sorted(OUT.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        if f.name.endswith(".signals.json"):
            continue
        try:
            v = json.loads(f.read_text())
        except ValueError:
            continue
        if "verdict" in v:
            rows.append({"file": f.stem, "url": v["url"], "product": v["product_name"], "verdict": v["verdict"],
                         "confidence": v["confidence"]})
    return rows[:30]


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body, ctype="application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/recent":
            return self.send(200, recent())
        if m := re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path):
            job = JOBS.get(m.group(1))
            return self.send(200, job) if job else self.send(404, {"error": "This run is no longer in memory."})
        if m := re.fullmatch(r"/api/results/([a-z0-9._-]+)", path):
            f = OUT / f"{m.group(1)}.json"
            return self.send(200, f.read_bytes()) if f.is_file() else self.send(404, {"error": "No saved result."})
        name = "index.html" if path == "/" else path.lstrip("/")
        f = WEB / name
        if "/" in name or f.suffix not in TYPES or not f.is_file():
            return self.send(404, {"error": "Not found."})
        self.send(200, f.read_bytes(), TYPES[f.suffix])

    def do_POST(self) -> None:
        if self.path != "/api/jobs":
            return self.send(404, {"error": "Not found."})
        if self.headers.get("Origin", f"http://{self.headers.get('Host')}") != f"http://{self.headers.get('Host')}":
            return self.send(403, {"error": "Cross-site requests are not accepted."})
        try:
            url = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))).get("url", "").strip()
        except (ValueError, AttributeError):
            url = ""
        if not re.fullmatch(r"(https?://)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", url, re.I) or len(url) > 300:
            return self.send(400, {"error": "Enter a website address such as example.com."})
        if sum(not j["done"] for j in JOBS.values()) >= MAX_RUNNING:
            return self.send(429, {"error": f"{MAX_RUNNING} checks are already running. Wait for one to finish."})
        jid = uuid.uuid4().hex[:12]
        JOBS[jid] = job = {"id": jid, "url": normalize(url), "started": time.time(), "steps": [], "done": False,
                           "result": None}
        threading.Thread(target=run, args=(job,), daemon=True).start()
        self.send(202, {"id": jid})

    def log_message(self, *a) -> None:  # the run log on stderr is enough
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
