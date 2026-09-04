# Architecture

This document describes how the components of the perimeter person-detection alarm fit together. Read this if you want to understand the system before installing it, or if something is misbehaving and you need to debug it.

## Components at a glance

```
                   ┌──────────────────┐
                   │   Frigate NVR    │
                   │  (Coral EdgeTPU  │
                   │   recommended)   │
                   └────────┬─────────┘
                            │ MQTT
                            │ topic: frigate/events
                            │ payload: end
                            ▼
                   ┌──────────────────┐
                   │   Home Assistant │
                   │  automation:     │
                   │  alarme_perimetro│
                   │  (MQTT trigger)  │
                   └────────┬─────────┘
                            │ 10-min cooldown
                            │ per camera
                            ▼
                   ┌──────────────────┐         ┌─────────────┐
                   │  notify.* push   │────────▶│  Telegram   │
                   │  (iOS/Android)   │         │  (annotation│
                   └──────────────────┘         │   + clip)   │
                                               └─────────────┘
```

The advanced-mode interior alarm sits alongside the perimeter alarm with an extra gate:

```
                   ┌──────────────────┐
                   │  input_text      │
                   │  zona_alarme     │
                   │  "internal|..."  │  ← user sets this manually
                   └────────┬─────────┘
                            │ condition check
                            ▼
                   ┌──────────────────┐
                   │  alarme_interno  │
                   │  _avancado       │
                   │  (interior cams) │
                   └──────────────────┘
```

## What each piece does

### Frigate

- Runs as an NVR, processes RTSP streams from your IP cameras
- A Coral EdgeTPU is the recommended accelerator but any supported detector works
- Emits MQTT events on `frigate/events` whenever a person (or other object) is detected
- Each event has a unique ID that can be used to fetch its snapshot and clip

Frigate is **not** modified by this repo. The provided `frigate_config_snippet.yaml` is a small subset of settings that improve person-detection precision; merge it into your existing config, don't replace your config with it.

### MQTT broker

- A Mosquitto broker (typically the one bundled with the Home Assistant Mosquitto add-on)
- Frigate publishes to it; Home Assistant subscribes
- The trigger topic in this project is `frigate/events`, the payload of interest is `end` (Frigate emits `new` mid-event and `end` once the event has concluded and the clip is fully encoded)

If the MQTT integration in Home Assistant is ever restarted (either manually or by an HA reboot), it loses its subscription to the broker. The reload-config-entry service call usually re-establishes it within seconds; see `references/troubleshooting.md` for the exact command.

### Home Assistant automations

Two automations ship with this project:

1. **`alarme_perimetro`** — fires on `frigate/events` for any person event whose camera is in the user's "perimeter" list. Conditions check: per-camera 10-minute cooldown; the user must have the alarm enabled (`input_boolean.alarme_interno == on`); the event must have a clip; the label must be `person`. When it fires, it sends a push notification with a video thumbnail pointing to `/api/frigate/events/<id>/clip.mp4`.

2. **`alarme_interno_avancado`** — fires for interior cameras (bedroom, living room, etc.) but only when the user has explicitly enabled advanced mode. The gate is the `input_text.zona_alarme` entity: when its value starts with `internal|`, interior cameras also push; when it starts with anything else, they don't. This is how a household distinguishes "I'm home, I want doorbell alerts but not interior cams" from "I'm leaving, alert me on everything".

Both automations are pure HA YAML. They are pushed to HA via the REST API by the installer.

### Telegram bot

Used for **proactive delivery** of annotated clips. When the perimeter alarm fires, the user gets a normal push notification from the HA Companion app, but the agent (if running) also receives the event via the HA logbook and can fetch the snapshot, vision-analyse it, draw a bold red bounding box, and post the result to a Telegram chat. This is what makes the user able to see "is this a real person or a tree branch?" in a single artefact without opening the Frigate UI.

The Telegram bot token and chat ID are asked for at install time and stored in `.env`. They are never committed to the repo (see `.gitignore`).

### The annotation script

`scripts/annotate_clip.py` takes a Frigate event ID, fetches the snapshot (with the Frigate-drawn orange box), vision-analyses it, and produces:

- A JPEG with a bold red rectangle drawn over the suspect area plus a verdict label at the top
- An 8-second looped MP4 with the same overlay burned in over the original clip

It uses Pillow for image work and ffmpeg for video overlay. The vision step uses whatever vision model your agent has configured — the script is provider-agnostic and just expects a function that accepts an image URL and returns a one-line verdict.

## Data flow for a single event

1. Frigate detects a person, event is created, MQTT `new` published
2. Event ends (person leaves frame, or 30s with no detection), Frigate encodes the clip, MQTT `end` published
3. HA `alarme_perimetro` automation fires
4. Conditions evaluated: is the alarm on? is the camera in the perimeter list? is the per-camera cooldown elapsed? did the previous event of this camera finish? is the label `person`?
5. If all conditions pass, HA sends a push notification to all `notify.mobile_app_*` entities, with the video URL `/api/frigate/events/<id>/clip.mp4` so the iOS Companion app shows a video preview inline
6. Simultaneously, the agent (if running) detects the trigger from the HA logbook, calls the annotation script, and posts the result to Telegram
7. The alarm state is recorded: `input_text.zona_alarme` is set to `camera|timestamp` so the next event of the same camera within 10 minutes is suppressed

## Why a 10-minute cooldown?

A person loitering in a doorway, a delivery driver leaving a package, a child playing in the yard — all of these can produce many person events over a short time if the detector is sensitive enough. Without a cooldown, the user gets a notification every 30 seconds for the duration of the event. The cooldown is per-camera so a person moving from the front door to the backyard will still generate one alert for each camera, but a person staying in the front yard for 5 minutes will only generate one alert total.

The 10-minute value was chosen empirically. Lower values (5 min) feel responsive but let bursts through. Higher values (30 min) feel sluggish. Adjust to taste in the HA automation YAML after install.

## Why two automations, not one?

The user originally had a single alarm automation that triggered for every camera. The downside: interior cameras (bedroom, living room) also pushed, which the user found noisy when at home. Splitting into "perimeter" (always-on) and "interior advanced" (gated) gave them the "doorbell-like" experience they wanted for the perimeter plus a manual override for the times they wanted interior coverage too.
