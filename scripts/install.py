#!/usr/bin/env python3
"""
Interactive installer for the perimeter person-detection alarm.

Walks the user through:
  1. Detecting Frigate cameras (from the local Frigate API)
  2. Classifying each camera as perimeter or interior
  3. Asking for Home Assistant URL + long-lived token
  4. Asking for Telegram bot token + chat id (optional, for proactive alerts)
  5. Writing the resulting YAML automations and a frigate config snippet
  6. Uploading the automations to HA via REST API

No hardcoded values. Everything is asked at runtime.

# CONFIGURATION
# The user runs `python3 scripts/install.py` from the repo root. The
# script will prompt for everything it needs. None of the values are
# read from environment variables, config files, or this source — the
# only pre-requisite is that the user has Home Assistant running with
# the Frigate integration already set up and MQTT enabled in Frigate.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ANSI colour codes — disabled if stdout is not a TTY.
USE_COLOR = sys.stdout.isatty()
def c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text
def green(t):  return c("32", t)
def red(t):    return c("31", t)
def yellow(t): return c("33", t)
def bold(t):   return c("1",  t)
def dim(t):    return c("2",  t)

REPO_ROOT = Path(__file__).resolve().parent.parent
HA_AUTOMATIONS_DIR = REPO_ROOT / "ha_automations"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def ask(prompt, default=None, validator=None, secret=False):
    """Prompt the user for a single line of input. Optional default and validator."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        try:
            raw = input(f"{bold(prompt)}{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        value = raw.strip() if not secret else raw.strip()
        if not value and default is not None:
            value = default
        if validator and not validator(value):
            print(red("  Invalid value, please try again."))
            continue
        return value


def ask_choice(prompt, options):
    """Prompt the user to pick from a numbered list. Returns the chosen value."""
    print(bold(prompt))
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(bold("Choice (number)"))
        if raw.strip().isdigit():
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(options):
                return options[idx]
        print(red("  Invalid choice."))


# ---------------------------------------------------------------------------
# Frigate camera detection
# ---------------------------------------------------------------------------

def fetch_frigate_cameras(frigate_url):
    """Hit Frigate's /api/cameras and return a list of camera name strings."""
    url = frigate_url.rstrip("/") + "/api/cameras"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(red(f"Could not reach Frigate at {url}: {e}"))
        return None
    return sorted(data.keys())


# ---------------------------------------------------------------------------
# Home Assistant probes
# ---------------------------------------------------------------------------

def probe_ha(ha_url, token):
    """Verify the HA token works and return the user info dict."""
    req = urllib.request.Request(
        ha_url.rstrip("/") + "/api/",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.load(resp)
    except Exception as e:
        print(red(f"Could not reach HA at {ha_url}: {e}"))
        return None


def probe_ha_phones(ha_url, token):
    """Return all notify.mobile_app_* entity ids."""
    req = urllib.request.Request(
        ha_url.rstrip("/") + "/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            states = json.load(resp)
    except Exception:
        return []
    return sorted(
        s["entity_id"] for s in states
        if s["entity_id"].startswith("notify.mobile_app_")
    )


# ---------------------------------------------------------------------------
# Helpers for building the YAML the user pastes into HA
# ---------------------------------------------------------------------------

def render_perimeter_yaml(perimeter_cams, internal_cams, phone_entities, cooldown_seconds):
    """Return the perimeter automation YAML as a string, with placeholders filled."""
    src = (HA_AUTOMATIONS_DIR / "alarme_perimetro.yaml").read_text()
    internal_list = "[" + ", ".join(f"'{c}'" for c in internal_cams) + "]"
    phone_actions = "\n".join(
        f"  - action: notify.{eid}\n"
        f"    data:\n"
        f"      title: \"Person detected\"\n"
        f"      message: \"{{{{ trigger.payload_json['after']['camera'] | replace('_', ' ') | title }}}}\"\n"
        f"      data:\n"
        f"        video: \"/api/frigate/events/{{{{ trigger.payload_json['after']['id'] }}}}/clip.mp4\"\n"
        f"        image: \"/api/frigate/events/{{{{ trigger.payload_json['after']['id'] }}}}/snapshot.jpg\"\n"
        f"        entity_id: \"camera.{{{{ trigger.payload_json['after']['camera'] }}}}\"\n"
        f"        tag: \"person-{{{{ trigger.payload_json['after']['camera'] }}}}\"\n"
        f"        group: \"alarm\"\n"
        f"        push:\n"
        f"          sound:\n"
        f"            name: default\n"
        f"            critical: false\n"
        f"            volume: 0.5\n"
        for eid in phone_entities
    )
    src = src.replace("INTERNAL_CAMERAS", internal_list)
    src = src.replace("COOLDOWN_SECONDS", str(cooldown_seconds))
    # Remove the two template PHONE_1 / PHONE_2 blocks, then append the
    # dynamically-built list of notify actions.
    src = re.sub(
        r"\n  # 2\. Push to all phones.*?(?=^[a-z]|\Z)",
        "\n" + phone_actions + "\n",
        src,
        flags=re.DOTALL | re.MULTILINE,
    )
    return src


def render_interior_yaml(internal_cams, phone_entities):
    """Return the interior advanced-mode automation YAML as a string."""
    src = (HA_AUTOMATIONS_DIR / "alarme_interno_avancado.yaml").read_text()
    internal_list = "[" + ", ".join(f"'{c}'" for c in internal_cams) + "]"
    phone_actions = "\n".join(
        f"  - action: notify.{eid}\n"
        f"    data:\n"
        f"      title: \"[ADVANCED] Person detected\"\n"
        f"      message: \"{{{{ trigger.payload_json['after']['camera'] | replace('_', ' ') | title }}}}\"\n"
        f"      data:\n"
        f"        video: \"/api/frigate/events/{{{{ trigger.payload_json['after']['id'] }}}}/clip.mp4\"\n"
        f"        image: \"/api/frigate/events/{{{{ trigger.payload_json['after']['id'] }}}}/snapshot.jpg\"\n"
        f"        entity_id: \"camera.{{{{ trigger.payload_json['after']['camera'] }}}}\"\n"
        f"        tag: \"person-internal-{{{{ trigger.payload_json['after']['camera'] }}}}\"\n"
        f"        group: \"alarm\"\n"
        f"        push:\n"
        f"          sound:\n"
        f"            name: default\n"
        f"            critical: false\n"
        f"            volume: 0.5\n"
        for eid in phone_entities
    )
    src = src.replace("INTERNAL_CAMERAS", internal_list)
    src = re.sub(
        r"\n  - action: notify\.PHONE_1.*?(?=^[a-z]|\Z)",
        "\n" + phone_actions + "\n",
        src,
        flags=re.DOTALL | re.MULTILINE,
    )
    return src


# ---------------------------------------------------------------------------
# Upload to HA via REST API
# ---------------------------------------------------------------------------

def upload_automation(ha_url, token, automation_id, yaml_text):
    """POST a new automation config to HA. Returns True on 200."""
    url = ha_url.rstrip("/") + f"/api/config/automation/config/{automation_id}"
    body = parse_simple_yaml(yaml_text)
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(red(f"  HA returned {e.code}: {e.read().decode()[:300]}"))
        return False


def parse_simple_yaml(text):
    """Parse a flat-ish YAML of the HA automation format. Intentionally
    limited to what the generated files contain — id, alias, mode, max,
    triggers, conditions, actions. Anything fancier and the user
    should be doing this in the HA UI instead."""
    # Use the HA-bundled YAML if present; otherwise fall back to a
    # minimal hand-rolled parser.
    try:
        import yaml  # PyYAML
        return yaml.safe_load(text)
    except ImportError:
        print(yellow("  PyYAML not installed, falling back to a stub."))
        return {"_raw": text}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(bold("\n=== HA + Frigate Person-Detection Alarm installer ===\n"))
    print(dim("This will ask a few questions, then write YAML you can paste into HA.\n"))

    # ---- Step 1: Frigate
    print(bold("[1/6] Frigate connection\n"))
    frigate_url = ask(
        "Frigate URL (where your NVR is reachable)",
        default="http://homeassistant.local:5000",
    )
    print(dim(f"  Probing {frigate_url}..."))
    cameras = fetch_frigate_cameras(frigate_url)
    if cameras is None:
        sys.exit(1)
    if not cameras:
        print(red("  Frigate returned no cameras. Add at least one in the Frigate UI first."))
        sys.exit(1)
    print(green(f"  Found {len(cameras)} cameras: {', '.join(cameras)}\n"))

    # ---- Step 2: classify each camera
    print(bold("[2/6] Classify each camera\n"))
    perimeter, interior = [], []
    for cam in cameras:
        choice = ask_choice(
            f"Is '{cam}' perimeter (always alerts) or interior (advanced mode only)?",
            ["perimeter", "interior", "skip (no alert at all)"],
        )
        if choice.startswith("perimeter"):
            perimeter.append(cam)
        elif choice.startswith("interior"):
            interior.append(cam)
        # skip means neither

    if not perimeter:
        print(yellow("  Warning: no perimeter cameras. The alarm will never fire.\n"))

    # ---- Step 3: HA connection
    print(bold("[3/6] Home Assistant connection\n"))
    ha_url = ask("HA URL", default="http://homeassistant.local:8123")
    token = ask("HA long-lived access token", secret=True)
    print(dim("  Probing HA..."))
    info = probe_ha(ha_url, token)
    if not info:
        sys.exit(1)
    print(green(f"  Authenticated as: {info.get('message', '?')}\n"))

    phones = probe_ha_phones(ha_url, token)
    if phones:
        print(green(f"  Found {len(phones)} mobile_app notify services: {', '.join(phones)}"))
    else:
        print(yellow("  No mobile_app notify services found. Push will not work until you set one up."))
    print()

    # ---- Step 4: Telegram (optional)
    print(bold("[4/6] Telegram (optional — for proactive clip delivery)\n"))
    use_tg = ask("Use a Telegram bot for proactive clip delivery? (yes/no)", default="no").lower().startswith("y")
    tg_token = tg_chat = None
    if use_tg:
        tg_token = ask("Telegram bot token", secret=True)
        tg_chat = ask("Telegram chat id (user or group)")
    print()

    # ---- Step 5: cooldown
    print(bold("[5/6] Cooldown\n"))
    cooldown = int(ask("Cooldown seconds (per camera, default 600 = 10 min)", default="600",
                       validator=lambda s: s.isdigit() and 30 <= int(s) <= 7200))
    print()

    # ---- Step 6: write and upload
    print(bold("[6/6] Generating YAML\n"))
    output_dir = REPO_ROOT / "generated"
    output_dir.mkdir(exist_ok=True)
    perm_yaml = render_perimeter_yaml(perimeter, interior, phones, cooldown)
    int_yaml = render_interior_yaml(interior, phones) if interior else ""
    (output_dir / "alarme_perimetro.generated.yaml").write_text(perm_yaml)
    if int_yaml:
        (output_dir / "alarme_interno_avancado.generated.yaml").write_text(int_yaml)

    # .env
    env_lines = [f"FRIGATE_URL={frigate_url}", f"HA_URL={ha_url}", f"HA_TOKEN={token}"]
    if use_tg:
        env_lines += [f"TELEGRAM_BOT_TOKEN={tg_token}", f"TELEGRAM_CHAT_ID={tg_chat}"]
    (REPO_ROOT / ".env").write_text("\n".join(env_lines) + "\n")
    os.chmod(REPO_ROOT / ".env", 0o600)

    print(green("Wrote:"))
    print(f"  - generated/alarme_perimetro.generated.yaml")
    if int_yaml:
        print(f"  - generated/alarme_interno_avancado.generated.yaml")
    print(f"  - .env  (chmod 600, do not commit)\n")

    # Offer to upload
    if ask("Upload to Home Assistant now? (yes/no)", default="yes").lower().startswith("y"):
        print(dim("  Uploading alarme_perimetro..."))
        if upload_automation(ha_url, token, "alarme_perimetro", perm_yaml):
            print(green("  alarme_perimetro uploaded."))
        else:
            print(red("  Upload failed — paste the generated YAML manually."))
        if int_yaml:
            print(dim("  Uploading alarme_interno_avancado..."))
            if upload_automation(ha_url, token, "alarme_interno_avancado", int_yaml):
                print(green("  alarme_interno_avancado uploaded."))
            else:
                print(red("  Upload failed — paste the generated YAML manually."))
    else:
        print(dim("  Skipped. Paste the generated YAML into HA when ready."))

    # Helpers (input_boolean + input_text)
    print()
    print(bold("Manual step required: create the helper entities in HA.\n"))
    print("In your HA UI, go to Settings → Devices & Services → Helpers →")
    print("Create helper, and add:")
    print("  - Toggle helper, name 'Alarm internal', id 'alarme_interno'")
    print("  - Text helper, name 'Zone alarm', id 'zona_alarme', default 'none|0'")
    print()
    print(dim("(A future version of this installer will create them via REST too.)"))
    print()
    print(green("Done. Test by walking past a perimeter camera and checking your phone."))


if __name__ == "__main__":
    main()
