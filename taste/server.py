"""Web UI: python -m taste.server [port | /path/to.sock].

Binds to localhost, or to a unix socket if given a path; put a reverse proxy with TLS in front to publish it. Set TASTE_TOKEN to require an
access code for starting checks (reading finished results stays open), which a public deployment needs:
every check spends the server's Claude quota.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import signal
import socket
import socketserver
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
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8"}
JOBS: dict[str, dict] = {}
ARGS = SimpleNamespace(model=agent.MODEL, effort=agent.EFFORT, no_repair=False, no_support_check=False)
MAX_RUNNING = int(os.environ.get("TASTE_MAX_RUNNING", "4"))
TOKEN = os.environ.get("TASTE_TOKEN", "")
DRAINING = threading.Event()  # set on SIGTERM: finish running checks, accept no new ones, then exit


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
        if isinstance(v, dict) and v.get("verdict") in cli.COLOURS:
            rows.append({"file": f.stem, "url": v["url"], "product": v["product_name"], "verdict": v["verdict"]})
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
        if path == "/api/config":
            return self.send(200, {"static": False, "token_required": bool(TOKEN)})
        if path == "/api/recent":
            return self.send(200, recent())
        if m := re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path):
            job = JOBS.get(m.group(1))
            return self.send(200, job) if job else self.send(404, {"error": "This run is no longer in memory."})
        if m := re.fullmatch(r"/api/results/([a-z0-9._-]+)", path):
            f = OUT / f"{m.group(1)}.json"
            if ".." in m.group(1):
                return self.send(404, {"error": "No saved result."})
            return self.send(200, f.read_bytes()) if f.is_file() else self.send(404, {"error": "No saved result."})
        if m := re.fullmatch(r"/api/report/([a-z0-9._-]+)\.pdf", path):
            name, src = m.group(1), OUT / f"{m.group(1)}.json"
            if ".." in name or not src.is_file():
                return self.send(404, {"error": "No saved result."})
            from . import report  # one-product report, built on request
            work = OUT / "pdf" / name
            work.mkdir(parents=True, exist_ok=True)
            (work / "verdicts.json").write_text(json.dumps([json.loads(src.read_text())]))
            sig = OUT / f"{name}.signals.json"
            for extra_file in (sig, OUT / f"{name}.png", OUT / f"{name}.docs.png"):
                if extra_file.is_file():
                    (work / extra_file.name).write_bytes(extra_file.read_bytes())
            made = report.build(work)
            if made.suffix != ".pdf":
                return self.send(200, made.read_bytes(), "text/html; charset=utf-8")
            return self.send(200, made.read_bytes(), "application/pdf")
        name = "index.html" if path == "/" else path.lstrip("/")
        f = WEB / name
        if "/" in name or f.suffix not in TYPES or not f.is_file():
            return self.send(404, {"error": "Not found."})
        self.send(200, f.read_bytes(), TYPES[f.suffix])

    def do_POST(self) -> None:
        if self.path != "/api/jobs":
            return self.send(404, {"error": "Not found."})
        origin = self.headers.get("Origin")  # browsers send it on POST; it must be this site, whatever the scheme
        if origin and origin.split("://", 1)[-1] != self.headers.get("Host"):
            return self.send(403, {"error": "Cross-site requests are not accepted."})
        if TOKEN and not hmac.compare_digest(self.headers.get("X-Access-Code", ""), TOKEN):
            return self.send(401, {"error": "That access code is not valid."})
        try:
            url = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))).get("url", "").strip()
        except (ValueError, AttributeError):
            url = ""
        if not re.fullmatch(r"(https?://)?[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?", url, re.I) or len(url) > 300:
            return self.send(400, {"error": "Enter a website address such as example.com."})
        if DRAINING.is_set():
            return self.send(503, {"error": "An update is being installed. Try again in a minute."})
        if sum(not j["done"] for j in JOBS.values()) >= MAX_RUNNING:
            return self.send(429, {"error": f"{MAX_RUNNING} checks are already running. Wait for one to finish."})
        jid = uuid.uuid4().hex[:12]
        JOBS[jid] = job = {"id": jid, "url": normalize(url), "started": time.time(), "steps": [], "done": False,
                           "result": None}
        threading.Thread(target=run, args=(job,), daemon=True).start()
        self.send(202, {"id": jid})

    def log_message(self, *a) -> None:  # the run log on stderr is enough
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):  # the handler logs and reads client_address as a (host, port) pair
        conn, _ = super().get_request()
        return conn, ("unix", 0)


def drain(srv, grace: float = 6.0) -> None:
    """Stop for a restart without cutting off a check: wait for running ones, give browsers a
    moment to collect the result, then shut down. The unit's TimeoutStopSec bounds the wait."""
    if DRAINING.is_set():
        return
    DRAINING.set()

    def wait() -> None:
        running = sum(not j["done"] for j in JOBS.values())
        if running:
            print(f"stop requested: waiting for {running} running check(s)", file=sys.stderr, flush=True)
            while any(not j["done"] for j in JOBS.values()):
                time.sleep(1)
            time.sleep(grace)
        srv.shutdown()

    threading.Thread(target=wait, daemon=True).start()


def main() -> None:
    where = sys.argv[1] if len(sys.argv) > 1 else "8787"
    OUT.mkdir(parents=True, exist_ok=True)
    if where.isdigit():
        srv = ThreadingHTTPServer(("127.0.0.1", int(where)), Handler)
        print(f"http://127.0.0.1:{where}", flush=True)
    else:
        Path(where).unlink(missing_ok=True)
        srv = UnixServer(where, Handler)
        os.chmod(where, 0o660)  # the reverse proxy reaches it through group permission
        print(f"unix:{where}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: drain(srv))
    srv.serve_forever()


if __name__ == "__main__":
    main()
