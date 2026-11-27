"""A headless Chromium for the two things plain HTTP cannot do: see the page and run its JavaScript.

Uses the browser Playwright already downloaded; set TASTE_CHROME to point somewhere else.
Without a browser the agent still works, it just has no screenshot.
"""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

_CANDIDATES = ("~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
               "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
               "/opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
               "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def binary() -> str | None:
    if os.environ.get("TASTE_CHROME"):
        return os.environ["TASTE_CHROME"]
    for pat in _CANDIDATES:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


def _run(args: list[str], timeout: int = 45) -> subprocess.CompletedProcess | None:
    from .collect import is_public  # imported here: collect imports this module
    exe = binary()
    if not exe or not is_public(args[-1]):
        return None
    try:
        return subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                               "--virtual-time-budget=8000", *args], capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


def screenshot(url: str, path: Path, width: int = 1280, height: int = 2000) -> bool:
    """One page load. Returns True if a PNG of a plausible size was written."""
    path.unlink(missing_ok=True)
    _run([f"--window-size={width},{height}", f"--screenshot={path}", url])
    return path.is_file() and path.stat().st_size > 5_000


def rendered_html(url: str) -> str:
    """The page's DOM after its scripts ran, for sites that send an empty shell."""
    p = _run(["--dump-dom", url])
    return p.stdout.decode("utf-8", "replace") if p and p.returncode == 0 else ""
