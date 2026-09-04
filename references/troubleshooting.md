# Troubleshooting

This is a list of failure modes that have been observed in real installations, with the exact fix. If something is broken, look here first.

## The alarm never fires

### Symptom
A real person walks past a perimeter camera, but the phone gets no push notification.

### Likely causes
1. The MQTT integration in Home Assistant is not subscribed to the broker.
2. The Frigate event has no clip (clip still being encoded when HA fires).
3. The label is not `person` (Frigate might have detected `dog` or `cat`).
4. The input_boolean `alarme_interno` is off.
5. The cooldown is still active (someone just triggered a push on this camera).

### How to diagnose
1. In HA, go to Developer Tools → Events → Listen to `frigate/events` and trigger a real person. You should see a payload arrive within a second.
2. In HA, go to Settings → Devices & Services → MQTT → ⋮ → Reload. If the integration was stale, this fixes it.
3. Check the YAML of your automation in HA. Look at the last triggered timestamp — if it never updates, the trigger is not firing.
4. In HA, go to Settings → Devices & Services → Helpers and confirm `input_boolean.alarme_interno` is on.

## The alarm fires constantly for the same camera

### Symptom
You get one push notification, then a second 30 seconds later for the same camera, with the same event still in progress.

### Likely cause
The cooldown in `input_text.zona_alarme` is not being written. Either the action that sets the text is failing, or the value is being overwritten by another automation.

### How to diagnose
1. In HA, go to Developer Tools → States → `input_text.zona_alarme`. The value should look like `camera_name|1234567890.123` after a push.
2. If the value is `none|0` or empty, the `input_text.set_value` action in the perimeter alarm did not run. Check the trace in Developer Tools → Actions.
3. If the value flips back to `none|0` between events, something is writing to it (probably another automation). Find it via Settings → Automations → filter by entity.

## Push notifications arrive but the video preview is blank

### Symptom
The iOS Companion app shows the notification with a "Video" attachment, but tapping it shows a 404 or a black box.

### Likely cause
The path `/api/frigate/events/<id>/clip.mp4` is wrong for your HA proxy version, or the Companion app is not on the same network as HA and cannot resolve the relative path.

### How to diagnose
1. On the HA host, run:
   ```bash
   curl -sI "http://homeassistant.local:8123/api/frigate/events/<id>/clip.mp4" \
        -H "Authorization: Bearer YOUR_HA_TOKEN" | head -1
   ```
   You should see `HTTP/1.1 200`. If 404, the path is wrong — check the Frigate integration version in HA (this project assumes 0.16+ with the events path, not the notifications path).
2. If you access HA via Tailscale Funnel or another HTTPS gateway, set up an absolute video URL in the automation (`https://...`) instead of the relative `/api/frigate/...` path. The Companion app may not resolve relative paths correctly when the app is not on the same network as HA.

## The Telegram clip arrives but the verdict is wrong

### Symptom
`scripts/annotate_clip.py` posts to Telegram with `FAKE: tree` but the snapshot clearly shows a person, or vice versa.

### Likely cause
The vision backend (OpenAI, etc.) is misinterpreting the snapshot. The bold red box is large and may be confusing the model if the actual subject is small inside the box.

### How to fix
1. Tighten the bold red box coordinates in `scripts/annotate_clip.py`. The current values (`0.85, 0.10, 1.00, 0.50` in normalized image coords) assume the false-positive region is the right edge. If your camera's suspect area is elsewhere, adjust the four numbers.
2. Use a stronger vision model. The default backend is `stub_vision`; switch to `openai_vision` (or any other) for real verdicts.
3. Add a "second opinion" — call two different vision models and only return FAKE if both say FAKE. The orchestration code goes in `scripts/annotate_clip.py`'s `main()`.

## The MQTT integration keeps losing its subscription

### Symptom
After every HA restart, the perimeter alarm stops firing until the user goes into Settings → Devices & Services → MQTT → Reload.

### Likely cause
The Mosquitto broker is not on the same host as HA, and HA's MQTT integration is dropping the connection on boot.

### How to fix
1. If Mosquitto runs as an HA add-on: verify the integration is set to "Use the broker on the Home Assistant host" in the integration settings.
2. If Mosquitto is external: the integration needs an explicit host/port. Check Settings → Devices & Services → MQTT → Configure.
3. As a workaround, schedule a `homeassistant.reload_config_entry` for the MQTT entry to run 30 seconds after HA start. Add this to your automations.yaml or a separate boot automation:
   ```yaml
   - alias: MQTT reload on HA start
     triggers:
       - trigger: homeassistant
         event: start
     actions:
       - delay: 30
       - action: homeassistant.reload_config_entry
         data:
           entry_id: YOUR_MQTT_ENTRY_ID_HERE
   ```
   You can find the entry_id by going to Developer Tools → Services → `homeassistant.reload_config_entry` and using the UI to pick the entity.

## The Frigate person detection is too sensitive

### Symptom
The alarm fires several times an hour, but on inspection the snapshots show trees, shadows, and other non-person objects with the orange box around them.

### How to fix
1. Edit `frigate_config_snippet.yaml` and increase `min_score` (start at 0.7) and `min_frames` (start at 5). Both make Frigate more conservative.
2. Add an `objects: mask:` polygon for the part of the frame where the false-positive source is (a tree, a flag pole, a window reflection). Coordinates are pixel (x1,y1, x2,y2) relative to your detection resolution.
3. Add a `motion: mask:` polygon for the same region. Even if Frigate detects a person there, suppressing motion detection there will prevent the event from being created at all.
4. As a last resort, add a `required_zones:` list to the perimeter alarm. Events that do not overlap the listed zone are not pushed.

## The Telegram bot token was leaked in chat

### What to do
1. Revoke the token immediately at https://my.telegram.org/apps or via BotFather (`/revoke`).
2. Generate a new token and store it in `.env` (which is `.gitignore`d) or in a secrets manager — never paste it into chat, ever.
3. If the leaked token had access to write in a public repo, the damage is done: assume any commits pushed with that token are known, audit the repo for any commits you don't recognize, and force-push a clean history if needed.
