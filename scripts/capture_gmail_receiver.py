"""Record the Gmail inbox proof for the demo video.

Two modes:

``--login``
    Opens a headed browser on a dedicated profile under ``.sessions/gmail`` and
    waits for you to sign in once. The profile is reused by every later run.

``--record``
    Opens the same profile, searches for the demo subject, opens the newest
    matching message and records the window to an MP4.

Like ``capture_receiver.py`` this is a build-side helper: it needs ``.sessions/``
and must never be run by the video agent. It blurs every element that renders a
real email address or phone number so the clip can go straight into the video.

Usage (PowerShell, from D:\\Reachout)::

    $env:REACHOUT_PRIVATE_IDS="local1,local2,919876543210"
    uv run python scripts/capture_gmail_receiver.py --login
    uv run python scripts/capture_gmail_receiver.py --dry-run
    uv run python scripts/capture_gmail_receiver.py --seconds 10
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent

SESSION_DIR = ROOT / ".sessions" / "gmail"
INBOX = "https://mail.google.com/mail/u/0/#inbox"

# Strings that must never be legible in a published frame: the operator's own
# address locals and phone numbers. They are deliberately kept out of the
# repository, so set them for the capture run instead, e.g.
#   $env:REACHOUT_PRIVATE_IDS="local1,local2,919876543210"
# Anything not listed still falls back to the generic phone-number regex below.
PRIVATE_IDS = [s.strip() for s in os.environ.get("REACHOUT_PRIVATE_IDS", "").split(",") if s.strip()]

# Google's UI is obfuscated, so selectors are only used for the stable shell.
# Anything that actually renders private text is caught by the text scan below.
BASE_CSS = """
header { background: #ffffff !important; }
"""

# Runs on load and after every mutation. Only elements whose *own* text nodes
# contain a private id are blurred, so the blur never swallows a whole card.
PRIVACY_JS = """
(ids => {
  const phone = /\\+\\d[\\d\\s()\\-]{6,}/;
  // Any address, so an unset REACHOUT_PRIVATE_IDS cannot leak an inbox.
  const email = /[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}/;
  const blur = () => {
    for (const el of document.querySelectorAll('body *')) {
      if (el.dataset.reachoutBlur === '1') continue;
      let own = '';
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) own += node.nodeValue;
      }
      if (!own) continue;
      const hit = ids.some(id => own.includes(id)) || phone.test(own) || email.test(own);
      if (!hit) continue;
      el.style.filter = 'blur(7px)';
      el.dataset.reachoutBlur = '1';
    }
  };
  let queued = false;
  const schedule = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; blur(); });
  };
  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true, subtree: true, characterData: true,
  });
  blur();
})(__PRIVATE_IDS__);
""".replace("__PRIVATE_IDS__", json.dumps(PRIVATE_IDS))


def _convert(webm: Path, out_path: Path, keep_last: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ERROR: ffmpeg not found on PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the tail: the head is Gmail loading, which must not ship.
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-sseof",
            f"-{max(1, keep_last)}",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(out_path),
        ],
        check=True,
    )


def _locks() -> list[Path]:
    return [p for p in (SESSION_DIR / "parent.lock", SESSION_DIR / "lock") if p.exists()]


def _launch_kwargs(args: argparse.Namespace) -> dict:
    return {
        "persistent_context": True,
        "user_data_dir": str(SESSION_DIR),
        "headless": False,
        "humanize": True,
        "os": "windows",
        "window": (args.width, args.height),
    }


def _wait_for_inbox(page) -> None:
    page.wait_for_selector('table[role="grid"], div[role="main"]', timeout=600_000)
    page.wait_for_timeout(2_000)


def _is_signed_out(page) -> bool:
    """Best-effort check for Google's login interstitial.

    Gmail shows the sign-in page in several shapes (account chooser, password
    form, or a redirect to accounts.google.com), so check the URL and the
    stable login affordances rather than one selector.
    """
    if "accounts.google.com" in page.url:
        return True
    for selector in ('input[type="password"]', 'a[href*="ServiceLogin"]', 'a[href*="signin"]'):
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:  # noqa: BLE001 - a detached page is not signed out evidence
            continue
    return False


def _login(args: argparse.Namespace) -> int:
    from camoufox.sync_api import Camoufox

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Sign in as the receiving account. Profile: {SESSION_DIR}")
    print("Waiting up to 10 minutes for the inbox...")
    with Camoufox(**_launch_kwargs(args)) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(INBOX, wait_until="domcontentloaded", timeout=120_000)
        try:
            _wait_for_inbox(page)
        except Exception as exc:  # noqa: BLE001 - surface the reason, not a trace
            print(f"ERROR: inbox never appeared ({type(exc).__name__})", file=sys.stderr)
            return 2
        print("Signed in. Closing the browser.")
    return 0


def _record(args: argparse.Namespace) -> int:
    from camoufox.sync_api import Camoufox

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_dir = Path(tempfile.mkdtemp(prefix="reachout-gmail-"))

    query = quote(f'subject:"{args.subject}"', safe="")
    target = f"https://mail.google.com/mail/u/0/#search/{query}"

    launch = _launch_kwargs(args)
    if not args.dry_run:
        launch["record_video_dir"] = str(video_dir)
        launch["record_video_size"] = {"width": args.width, "height": args.height}

    try:
        with Camoufox(**launch) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=120_000)

            if _is_signed_out(page):
                print(
                    "ERROR: Gmail profile is signed out; run --login first",
                    file=sys.stderr,
                )
                return 2

            _wait_for_inbox(page)
            page.add_style_tag(content=BASE_CSS)
            page.evaluate(PRIVACY_JS)
            page.wait_for_timeout(1_500)

            row = page.locator('tr:has(td[role="gridcell"])').first
            if args.open_first and row.count() > 0:
                row.click()
                page.wait_for_selector('h2, div[role="main"]', timeout=30_000)
                page.wait_for_timeout(2_000)
                page.evaluate(PRIVACY_JS)

            if args.dry_run:
                shot = out_path.with_suffix(".png")
                page.screenshot(path=str(shot))
                print(f"Saved {shot}")
            else:
                page.wait_for_timeout(args.seconds * 1000)
    except Exception:
        # Never leave a partial recording behind for the video pipeline.
        shutil.rmtree(video_dir, ignore_errors=True)
        raise

    if not args.dry_run:
        videos = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if not videos:
            print(f"ERROR: no video written under {video_dir}", file=sys.stderr)
            return 3
        _convert(videos[-1], out_path, args.seconds)
        shutil.rmtree(video_dir, ignore_errors=True)
        print(f"Saved {out_path}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Sign in and save the profile")
    parser.add_argument("--out", default="out/raw/gmail_receiver.mp4")
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument(
        "--subject",
        default="Quick question about",
        help="Gmail search subject; keeps private addresses out of the query box",
    )
    parser.add_argument(
        "--no-open",
        dest="open_first",
        action="store_false",
        help="Stay on the result list instead of opening the newest message",
    )
    parser.add_argument("--dry-run", action="store_true", help="Save one screenshot instead")
    args = parser.parse_args()

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    locks = _locks()
    if locks:
        print(
            "ERROR: Gmail profile is locked; close the browser using it first "
            f"({', '.join(str(p) for p in locks)})",
            file=sys.stderr,
        )
        return 1

    if args.login:
        return _login(args)
    return _record(args)


if __name__ == "__main__":
    raise SystemExit(main())
