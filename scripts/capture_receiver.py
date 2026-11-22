"""Record the receiver side of the demo WhatsApp conversation.

Launches the already-authenticated Camoufox profile for the receiver account,
opens the chat with the sender, hides the WhatsApp sidebar (which would expose
unrelated personal chats), and records the window to an MP4 for the demo video.

This is a build-side helper: it needs read access to ``.sessions/`` and must
never be run by the video agent, which is denied that directory. The output is
handed to the video agent under ``out/raw/``.

Usage (PowerShell, from D:\\Reachout)::

    uv run python scripts/capture_receiver.py --sender-id wa_919000000000 --chat 919000000001 --dry-run
    uv run python scripts/capture_receiver.py --sender-id wa_919000000000 --chat 919000000001 --seconds 12
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_ROOT = ROOT / ".sessions" / "whatsapp"

# Hide the chat list (PII) and the install banner; let the conversation fill the
# window so the capture can be dropped into a phone frame.
HIDE_CSS = """
#side { display: none !important; }
#main { flex: 1 1 auto !important; width: 100% !important; }
[data-testid="intro-md-beta-logo"] { display: none !important; }
[data-testid="chatlist-header"] { display: none !important; }
"""

# The chat header shows the contact's phone number, which must never be legible.
# Blur only elements whose own text looks like a phone number so the message
# bubbles and timestamps stay sharp.
PRIVACY_JS = """
(() => {
  const pattern = /\\+?\\d[\\d\\s()\\-]{7,}/;
  const blur = () => {
    for (const el of document.querySelectorAll('#main *')) {
      if (el.dataset.reachoutBlur === '1') continue;
      let own = '';
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) own += node.nodeValue;
      }
      if (!own || !pattern.test(own)) continue;
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
})();
"""


def _convert(webm: Path, out_path: Path, keep_last: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ERROR: ffmpeg not found on PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the tail: the head of the recording is WhatsApp Web loading and
    # any QR/loading screen, which must never reach the demo video.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sender-id", required=True, help="Receiver sender ID, e.g. wa_919000000000")
    parser.add_argument("--chat", required=True, help="Other side's phone number, digits only")
    parser.add_argument("--out", default="out/raw/whatsapp_receiver.mp4")
    parser.add_argument("--seconds", type=int, default=12)
    parser.add_argument("--width", type=int, default=430)
    parser.add_argument("--height", type=int, default=864)
    parser.add_argument("--dry-run", action="store_true", help="Save one screenshot instead of recording")
    args = parser.parse_args()

    session_dir = SESSION_ROOT / args.sender_id
    if not session_dir.exists():
        print(f"ERROR: session profile not found: {session_dir}", file=sys.stderr)
        return 1
    locks = [p for p in (session_dir / "parent.lock", session_dir / "lock") if p.exists()]
    if locks:
        print(
            f"ERROR: profile is locked; close any app using {args.sender_id} first "
            f"({', '.join(str(p) for p in locks)})",
            file=sys.stderr,
        )
        return 1

    from camoufox.sync_api import Camoufox

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_dir = Path(tempfile.mkdtemp(prefix="reachout-receiver-"))

    launch: dict = {
        "persistent_context": True,
        "user_data_dir": str(session_dir),
        "headless": False,
        "humanize": True,
        "os": "windows",
        "window": (args.width, args.height),
    }
    if not args.dry_run:
        launch["record_video_dir"] = str(video_dir)
        launch["record_video_size"] = {"width": args.width, "height": args.height}

    try:
        with Camoufox(**launch) as context:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                f"https://web.whatsapp.com/send?phone={args.chat}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )

            if page.locator('canvas[aria-label="Scan this QR code to link a device"]').count() > 0:
                print("ERROR: receiver session needs QR authentication", file=sys.stderr)
                return 2

            page.wait_for_selector("#main", timeout=90_000)
            page.wait_for_selector("#main header", timeout=30_000)
            page.add_style_tag(content=HIDE_CSS)
            page.evaluate(PRIVACY_JS)
            page.wait_for_timeout(3_000)

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


if __name__ == "__main__":
    raise SystemExit(main())
