# Frigate + Home Assistant Person-Detection Alarm

A reusable installer for a perimeter person-detection alarm that connects Home Assistant to a local Frigate NVR, suppresses false positives, and pushes a video preview to your phone when a real person is detected.

This repo is designed to be dropped into any Home Assistant + Frigate setup. Nothing is hardcoded. The installer walks you through detection, configuration, and upload.

## What you get

After running the installer, you have:

- A **perimeter alarm** that fires on every person detection from the cameras you marked as perimeter (front door, backyard, etc.)
- An **advanced-mode interior alarm** that only fires for interior cameras (bedroom, living room) when you explicitly turn it on
- A **10-minute per-camera cooldown** so a person loitering in frame does not spam your phone
- A **Frigate config snippet** that improves person-detection precision (higher confidence threshold, require multiple frames, etc.)
- An **optional Telegram integration** that posts an annotated clip with a verdict label to a chat of your choice

## Prerequisites

You need a working Home Assistant installation with:

- The Frigate integration set up (Settings → Devices & Services → Add Integration → Frigate)
- MQTT enabled in Frigate (`mqtt.enabled: true` in your `frigate.yml`)
- A Mosquitto broker reachable from Frigate — typically the one bundled with the HA Mosquitto add-on
- At least one IP camera already feeding Frigate and at least one `notify.mobile_app_*` entity set up in HA

You will also need:

- Python 3.10 or newer on the machine where you run the installer
- `ffmpeg` on the same machine (only required for the optional clip annotation, not for the basic alarm)
- A long-lived access token from your HA user profile (Profile → Long-Lived Access Tokens → Create Token)

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/ClaudioSobrinho/HA-Security-System.git
cd HA-Security-System
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs `requests`, `Pillow`, and `pyyaml` (used by the installer to parse and render the YAML automations).

### 3. Run the installer

```bash
python3 scripts/install.py
```

The installer will:

1. **Probe your Frigate** at the URL you give it. It hits `/api/cameras` to detect the cameras you already have set up.
2. **Ask you to classify each camera** as perimeter, interior, or skip. Perimeter cameras always trigger the alarm. Interior cameras only trigger when you turn on "advanced mode" manually. Skip means no alarm at all from that camera.
3. **Probe your Home Assistant** at the URL you give it. It uses your long-lived token to verify the connection and to find the `notify.mobile_app_*` services you have set up.
4. **Ask if you want a Telegram bot** for proactive clip delivery. This is optional — skip it if you do not care about annotated clips with verdict labels.
5. **Ask for the cooldown** in seconds. The default is 600 (10 minutes), which works for most households. Set lower (300 = 5 min) if you want fewer bursts, higher (1800 = 30 min) if you want a quieter phone.
6. **Write the generated YAML** to `generated/alarme_perimetro.generated.yaml` and (if you have interior cameras) `generated/alarme_interno_avancado.generated.yaml`. A `.env` file is also written with all your secrets — it is `.gitignore`d so it will never be committed.
7. **Optionally upload the automations to HA** via the REST API. If you skip this, the YAML files are still on disk for you to paste manually.

### 4. Create the helper entities in HA

The automations reference two helper entities that must exist in HA. Create them in Settings → Devices & Services → Helpers → Create helper:

- **Toggle helper**, name `Alarm internal`, id `alarme_interno`. This is the master arm/disarm switch. The alarm does nothing when this is off.
- **Text helper**, name `Zone alarm`, id `zona_alarme`, default `none|0`. This is the cooldown bookkeeping variable plus the "advanced mode" gate. The perimeter alarm writes `camera|timestamp` to it after each push. The interior alarm reads its prefix to decide whether advanced mode is on (`internal|...` = on, anything else = off).

### 5. Test it

Walk past one of your perimeter cameras. Within a few seconds you should get a push notification with an inline video preview. If you set up Telegram, you should also receive the annotated clip in the chat.

## Manual installation (without the installer)

If you prefer to wire this up by hand or want to understand what the installer is doing:

1. Copy `ha_automations/alarme_perimetro.yaml` to your HA instance (Settings → Automations → Create automation → Edit YAML).
2. Open the file in a text editor. Replace `INTERNAL_CAMERAS` with a list of your interior camera names as a Python list literal, e.g. `['bedroom_cam', 'living_room_cam']`. Replace `COOLDOWN_SECONDS` with the number of seconds. Replace `PHONE_1` and `PHONE_2` with your `notify.mobile_app_*` entity ids.
3. Repeat for `ha_automations/alarme_interno_avancado.yaml` if you want the interior advanced mode.
4. Create the two helper entities as described above.
5. Merge `frigate_config_snippet.yaml` into your existing `frigate.yml` and restart Frigate.

## What the installer writes

```
HA-Security-System/
├── README.md                          ← you are here
├── LICENSE                            ← MIT
├── .gitignore                         ← excludes .env, generated/, editor cruft
├── .env.example                       ← template for .env (never commit .env)
├── requirements.txt                   ← requests, Pillow, pyyaml
├── scripts/
│   ├── install.py                     ← the interactive installer
│   ├── annotate_clip.py               ← bold-red bbox + Telegram delivery
│   └── vision_backends/
│       ├── __init__.py
│       └── stub_vision.py             ← placeholder backend
├── ha_automations/
│   ├── alarme_perimetro.yaml          ← template, fill in INTERNAL_CAMERAS
│   └── alarme_interno_avancado.yaml   ← template, fill in INTERNAL_CAMERAS
├── frigate_config_snippet.yaml        ← merge into your frigate.yml
├── references/
│   ├── architecture.md                ← how the components fit together
│   └── troubleshooting.md             ← common failure modes and fixes
└── generated/                         ← created by the installer, gitignored
    ├── alarme_perimetro.generated.yaml
    └── alarme_interno_avancado.generated.yaml
```

## Optional: annotated clip delivery to Telegram

The base alarm pushes to your phone via the HA Companion app. If you also want an annotated clip with a bold red bounding box and a verdict label ("REAL: person" or "FAKE: tree") posted to a Telegram chat, set up the optional Telegram integration:

1. Create a bot via BotFather on Telegram. Note the bot token.
2. Get your chat id (send a message to the bot, then call `https://api.telegram.org/bot<token>/getUpdates` and read the `chat.id`).
3. In the installer, answer yes when asked about Telegram and provide the token and chat id.
4. The installer writes `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`.
5. To actually post clips, you need an agent (or a cron job) that calls `scripts/annotate_clip.py --event-id <id>` for each new alarm trigger. The agent is out of scope for this repo — see `references/architecture.md` for the design.

To use a real vision model instead of the stub backend, swap `scripts/vision_backends/stub_vision.py` for one that calls your vision API. The script in `annotate_clip.py` already takes a `--vision-backend` argument that accepts any dotted path to a function with the signature `(image_url: str, prompt: str) -> str`.

## License

MIT. See `LICENSE`.
