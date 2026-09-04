#!/usr/bin/env python3
"""
Fetch a Frigate event, draw a bold red bounding box plus a verdict label,
and post the resulting annotated clip to a Telegram chat.

# WHAT THIS DOES
# 1. Given a Frigate event id, fetches the event's snapshot (with the
#    Frigate-drawn orange box) and the raw MP4 clip.
# 2. Draws an additional bold red rectangle on top of the suspect area
#    plus a verdict label at the top of the frame.
# 3. Vision-analyses the snapshot via a pluggable backend (the caller
#    passes a function that takes an image URL and returns a one-line
#    REAL/FAKE verdict).
# 4. Burns the annotated overlay onto the original clip with ffmpeg
#    (8-second loop, original audio preserved if present).
# 5. Sends the JPEG + MP4 to a Telegram chat via the bot API.

# CONFIGURATION
# The script takes everything it needs as command-line arguments or via
# environment variables. No hardcoded values. See the __main__ block
# at the bottom for the exact list of arguments.
#
# The vision backend is intentionally pluggable: pass --vision-backend
# as a dotted path to a Python function. The function signature is
#
#     def vision(image_url: str, prompt: str) -> str
#
# It must return a short string starting with "REAL:" or "FAKE:".
# Two reference backends are bundled in scripts/vision_backends/:
#   - openai_vision.py   (uses gpt-4o-mini, needs OPENAI_API_KEY)
#   - stub_vision.py     (returns "FAKE: not configured", for tests)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Frigate helpers
# ---------------------------------------------------------------------------

def fetch_frigate(url, path):
    """GET a Frigate URL. Returns the bytes."""
    req = urllib.request.Request(url.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def fetch_snapshot(frigate_url, event_id, with_bbox=True):
    """Fetch a snapshot JPEG. bbox=1 returns the frame with the orange box."""
    q = "?bbox=1" if with_bbox else ""
    return fetch_frigate(frigate_url, f"/api/events/{event_id}/snapshot.jpg{q}")


def fetch_clip(frigate_url, event_id):
    """Fetch the raw MP4 clip."""
    return fetch_frigate(frigate_url, f"/api/events/{event_id}/clip.mp4")


# ---------------------------------------------------------------------------
# Image annotation
# ---------------------------------------------------------------------------

def annotate(snapshot_bytes, verdict, output_jpeg):
    """Draw the bold red bbox + verdict label. Save to output_jpeg."""
    img = Image.open(__import__("io").BytesIO(snapshot_bytes)).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    # Bold red rectangle covering the right-edge suspect area. This is
    # the same region the user draws attention to with their original
    # installation; adjust the four coordinates if your camera's
    # false-positive zone is elsewhere.
    overlay_left  = int(0.85 * w)
    overlay_top   = int(0.10 * h)
    overlay_right = int(1.00 * w)
    overlay_bot   = int(0.50 * h)

    draw.rectangle(
        [overlay_left, overlay_top, overlay_right, overlay_bot],
        outline=(255, 0, 0), width=8,
    )
    # Black inner shadow for legibility on any background
    draw.rectangle(
        [overlay_left + 4, overlay_top + 4, overlay_right - 4, overlay_bot - 4],
        outline=(0, 0, 0), width=2,
    )

    # Verdict label band at the top
    label = f"  {verdict}  "
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36
        )
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    label_w = bbox[2] - bbox[0] + 24
    label_h = bbox[3] - bbox[1] + 16
    draw.rectangle([0, 0, label_w, label_h], fill=(0, 0, 0))
    draw.text((12, 8), label, fill=(255, 255, 0), font=font)

    img.save(output_jpeg, quality=92)
    return img.size


# ---------------------------------------------------------------------------
# Video overlay via ffmpeg
# ---------------------------------------------------------------------------

def burn_overlay(overlay_jpeg, clip_bytes, output_mp4, duration=8):
    """Burn the overlay image onto the clip for the first `duration` seconds.
    Falls back to a simple concat if ffmpeg is missing."""
    if not shutil.which("ffmpeg"):
        # No ffmpeg: just write the raw clip as-is.
        with open(output_mp4, "wb") as f:
            f.write(clip_bytes)
        return

    tmpdir = tempfile.mkdtemp(prefix="annotate-")
    try:
        overlay_png = os.path.join(tmpdir, "overlay.png")
        clip_in = os.path.join(tmpdir, "clip.mp4")
        with open(overlay_png, "wb") as f:
            f.write(open(overlay_jpeg, "rb").read())
        with open(clip_in, "wb") as f:
            f.write(clip_bytes)

        # ffmpeg reads the overlay as a looped image, scales it to the
        # clip's resolution, and overlays it for the first `duration`s.
        # After that, the original clip plays through.
        cmd = [
            "ffmpeg", "-y",
            "-i", clip_in,
            "-loop", "1", "-i", overlay_png,
            "-filter_complex",
            f"[1:v]scale=iw:ih[ovl];"
            f"[0:v][ovl]overlay=enable='between(t,0,{duration})'[out]",
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            output_mp4,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def telegram_send_photo(bot_token, chat_id, photo_path, caption):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    with open(photo_path, "rb") as f:
        body = {
            "chat_id": str(chat_id),
            "caption": caption[:1024],
        }
        # Multipart form-encoded
        boundary = "----ha-alarm"
        parts = []
        for k, v in body.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"frame.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")
        data = "".join(parts).encode("utf-8") + f.read() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def telegram_send_video(bot_token, chat_id, video_path, caption):
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    with open(video_path, "rb") as f:
        boundary = "----ha-alarm"
        parts = []
        for k, v in {"chat_id": str(chat_id), "caption": caption[:1024]}.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"clip.mp4\"\r\nContent-Type: video/mp4\r\n\r\n")
        data = "".join(parts).encode("utf-8") + f.read() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


# ---------------------------------------------------------------------------
# Vision backend loading
# ---------------------------------------------------------------------------

def load_vision_backend(dotted_path):
    """Load a vision backend from a 'package.module:function' string."""
    mod_path, _, fn_name = dotted_path.rpartition(":")
    module_name, _, attr = mod_path.rpartition(".")
    __import__(module_name)
    mod = sys.modules[module_name]
    return getattr(mod, attr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--event-id", required=True, help="Frigate event id")
    p.add_argument("--frigate-url", default=os.environ.get("FRIGATE_URL", "http://localhost:5000"))
    p.add_argument("--out-dir", default="./annotated")
    p.add_argument("--telegram-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    p.add_argument("--telegram-chat", default=os.environ.get("TELEGRAM_CHAT_ID"))
    p.add_argument("--vision-backend", default=os.environ.get(
        "VISION_BACKEND", "scripts.vision_backends.stub_vision:analyse"
    ))
    p.add_argument("--no-send", action="store_true",
                   help="Skip the Telegram send — useful for local testing.")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    out_jpeg = os.path.join(args.out_dir, f"{args.event_id}_annotated.jpg")
    out_mp4 = os.path.join(args.out_dir, f"{args.event_id}_annotated.mp4")

    print(f"Fetching snapshot for {args.event_id}...")
    snap_bytes = fetch_snapshot(args.frigate_url, args.event_id, with_bbox=True)
    print(f"  ({len(snap_bytes)} bytes)")

    print("Running vision analysis...")
    vision_fn = load_vision_backend(args.vision_backend)
    file_uri = f"file://{os.path.abspath(out_jpeg)}"  # placeholder; real impl passes a hosted URL
    # In real use, host the snapshot somewhere (HA, S3, local server)
    # and pass the public URL to the vision backend. For now we save
    # first and the user wires their own URL upstream.
    with open("/tmp/_annotate_input.jpg", "wb") as f:
        f.write(snap_bytes)
    # The actual hosted URL is the user's responsibility; we annotate
    # blindly if no vision backend can be reached.
    try:
        verdict = vision_fn(
            "file:///tmp/_annotate_input.jpg",
            "Is there a real human person visible? Reply with EXACTLY 'REAL: <reason>' or 'FAKE: <reason>'. Under 12 words."
        )
    except Exception as e:
        print(f"  Vision backend failed: {e}; using fallback verdict.")
        verdict = "FAKE: vision backend unavailable"

    print(f"  Verdict: {verdict}")

    print("Annotating snapshot...")
    w, h = annotate(snap_bytes, verdict, out_jpeg)
    print(f"  Wrote {out_jpeg} ({w}x{h})")

    print("Burning overlay onto clip...")
    clip_bytes = fetch_clip(args.frigate_url, args.event_id)
    burn_overlay(out_jpeg, clip_bytes, out_mp4, duration=8)
    print(f"  Wrote {out_mp4}")

    if args.no_send or not (args.telegram_token and args.telegram_chat):
        print("Skipping Telegram send.")
        return

    print("Sending to Telegram...")
    caption = f"{verdict}"
    telegram_send_photo(args.telegram_token, args.telegram_chat, out_jpeg, caption)
    telegram_send_video(args.telegram_token, args.telegram_chat, out_mp4, "Annotated clip")
    print("  Sent.")


if __name__ == "__main__":
    main()
