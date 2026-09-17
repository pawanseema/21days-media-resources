# Today's Meditation API

`POST /api/recommendations/daily-meditation`

Stateless picker for the More tab. Clients own stickiness, 6AM day boundary, played state, and the last-21 exclude list.

## Request

```json
{
  "device_id": "uuid",
  "exclude": [{ "video_id": "...", "timestamp": "45:30" }],
  "limit": 1
}
```

## Response

```json
{
  "count": 1,
  "result": {
    "video_id": "...",
    "timestamp": "45:30",
    "section_title": "Meditation with Live Santoor music by Kirill",
    "video_title": "...",
    "summary": "...",
    "url": "...",
    "section_duration_seconds": 745,
    "next_guided_section_title": "Guided meditation ...",
    "next_guided_duration_seconds": 400,
    "practice_duration_seconds": 1145
  }
}
```

Empty pool: `{ "count": 0, "result": null }`.

## Selection

1. Load Chroma `timestamp_section` rows.
2. Keep music-meditation titles; drop bumpers and `exclude`.
3. Pick `hash(device_id) % pool`.
4. Attach next guided chapter on the same video for practice duration.

## Client rules (mobile + web)

- Persist `device_id`, active card, played timestamps, and last **21** history rows locally (SharedPreferences / `localStorage`).
- Stick to the active card until the user opens/plays it.
- After play, fetch a new recommendation only once the next **6:00 local** meditation day begins.
- Send `exclude` as history ∪ current active clip so replacements stay unique.
- Show practice length from `practice_duration_seconds` (music + following guided) when present.
- Do not auto-stop playback when that duration elapses.
