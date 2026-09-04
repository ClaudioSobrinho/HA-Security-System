# Frigate + HA Person-Detection Alarm

A reusable, interactive installer for a perimeter person-detection alarm that:

- Connects Home Assistant to a local Frigate NVR via the `frigate/events` MQTT topic
- Lets the user pick which cameras are perimeter (push immediately) and which are interior (advanced mode only)
- Sends annotated clips with bounding boxes via Telegram when a real person is detected
- Includes a 10-minute per-camera cooldown so a person loitering in frame doesn't spam notifications
- Falls back to an "advanced mode" the user enables manually to also monitor interior cameras

This repo ships with **no hardcoded values**. The installer asks for the local URLs, MQTT credentials, Telegram bot token, and the camera list at runtime. The result is a set of YAML automations the user pastes into HA plus a Frigate config snippet that improves person-detection precision.

## Quick start

```bash
# 1. Install Python deps
pip install requests pillow

# 2. Run the interactive installer
python3 scripts/install.py

# 3. Follow the prompts. The script writes:
#    - ha_automations.yaml      → paste into HA Settings → Automations
#    - frigate_config_snippet.yaml → paste into your Frigate config
#    - .env                      → keep this private, your Telegram token lives here

# 4. Reload HA automations and restart Frigate
```

The installer detects which Frigate cameras exist in your HA instance and lets you classify them one by one as perimeter or interior. It also generates the MQTT trigger topic, cooldown window, and clip-annotation script that posts to your Telegram chat.

## What you get

After running the installer you have:

| File | Purpose |
|---|---|
| `scripts/install.py` | Interactive CLI installer. Asks questions, writes the rest of the files. |
| `scripts/annotate_clip.py` | Pulls a Frigate event snapshot + clip, draws a bold red bbox, vision-analyses it, posts to Telegram. |
| `ha_automations/alarme_perimetro.yaml` | HA automation: perimeter person detection → push. |
| `ha_automations/alarme_interno_avancado.yaml` | HA automation: interior person detection → push only when advanced mode is on. |
| `frigate_config_snippet.yaml` | Snippet to merge into your existing `frigate.yml`. Adjusts person-detection thresholds and adds per-camera object masks. |
| `references/architecture.md` | How the components fit together, what each does. |
| `references/troubleshooting.md` | Common failure modes and fixes. |

## Repository layout

```
.
├── README.md                       # you are here
├── LICENSE                         # MIT
├── .gitignore                      # excludes .env, .venv, generated files
├── scripts/
│   ├── install.py                  # the interactive installer
│   └── annotate_clip.py            # bbox annotation + Telegram poster
├── ha_automations/                 # YAML you paste into HA
│   ├── alarme_perimetro.yaml
│   └── alarme_interno_avancado.yaml
├── frigate_config_snippet.yaml     # YAML you merge into frigate.yml
└── references/
    ├── architecture.md
    └── troubleshooting.md
```

## License

MIT. See `LICENSE`.
