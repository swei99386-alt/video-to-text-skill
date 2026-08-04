[English](README_EN.md) | [简体中文](./README.md)

# Video to Text Skill

Got a video link you want transcribed but don't want to watch the whole thing? Need a clean text transcript you can copy-paste? This Skill lets AI do it in one command.

The strategy is simple, but critical:

> **Always try existing subtitles first. Only fall back to AI transcription if subtitles don't exist.**

Most legitimate videos (YouTube talks, podcasts) already have subtitles — a few seconds to grab them and you're done, free. Only when there are no subtitles does it download the video and run GPU transcription. This rule saves you from downloading 450MB and waiting 30 minutes for a transcription you could have gotten in seconds.

## What you get

- **A video URL or local file** → a clean plain-text transcript at `完整文字稿.txt`.
- Supports **X / Twitter**, **YouTube**, **Bilibili**, and local audio/video files.
- Videos without subtitles are auto-transcribed on GPU (~18× realtime).
- Old 5G GPUs handle videos **up to 4h 35m without OOM** (verified).
- Supports Chinese (auto-simplified), English, and other major languages.

## Who is this for

**Anyone who wants a transcript**: hand the URL to Claude Code or any AI that can run commands, get the result in one shot.

**Claude Code / Codex / similar AI tooling users**: install this as a Skill. From now on, whenever you mention a video link, the AI knows what to do.

## Three-step quickstart

### 1. Install dependencies

```bash
# Required: video download + subtitle extraction
pip install yt-dlp ffmpeg

# Transcription (only used when no subtitles exist)
pip install faster-whisper torch
```

### 2. Tell AI what to do

```text
Transcribe this video for me: https://www.youtube.com/watch?v=xxxxxx
```

Or install as a Skill and the AI handles it automatically when it sees a video link.

### 3. Check the output on your Desktop

A folder appears on your Desktop with:

| File | Purpose |
|------|---------|
| `完整文字稿.txt` | Your final clean text transcript |
| `audio.srt` | Timestamped subtitles (for "when exactly was this said?") |
| `media.mp4` / `audio.wav` | Intermediate files, safe to delete |

## What it does NOT do

- **No translation** — only transcribes what was said. English stays English. For translation, use a separate AI.
- **No default full-video download** — only when no subtitles exist. This is core to the strategy.
- **Not 100% accurate** — transcription is "best-effort". Heavy accents, overlapping speakers, loud background music reduce quality.
- **Cannot reach encrypted / login-walled content** — WeChat video, subscription-only content is out of reach.

## Benchmarks (2026-08)

| Video length | Transcription time | GPU |
|---|---|---|
| 10s silence | 0.5s | OK |
| 2h 17m (English workshop) | **7m 23s** | 5G GPU, no OOM |
| 4h 35m (concatenation test) | **15m 18s** | 5G GPU, no OOM (no segmenting) |

**Speed: ~18× realtime** — 1 hour of audio takes ~3 minutes.

## Why "subtitles first"?

Once we made the mistake of downloading a 450MB video and running it through CPU transcription for half an hour, only to get text full of errors — when the video had perfectly good YouTube subtitles all along.

Since then this rule has been baked in:
1. **Always** `yt-dlp --write-subs --write-auto-subs` first
2. Got subtitles → done, seconds.
3. No subtitles → only then download media, extract audio, GPU transcribe.

This rule means **90% of videos cost nearly nothing**.

## File layout

```
video-to-text-skill/
├── README.md          ← Chinese
├── README_EN.md       ← English (you are here)
├── SKILL.md           ← Claude Code skill entrypoint
├── LICENSE
├── requirements.txt
└── scripts/
    └── grab.py        ← Core script (single command)
```

## Tuning

The constant `SEGMENT_SECONDS` in `scripts/grab.py` controls when segmenting kicks in (default: > 4h 35m). Adjust as needed.

- **More aggressive (less segmenting)**: bump it up, e.g. `999999`
- **More conservative (more segmenting)**: bump it down, e.g. `1800` (30 min)

## Contributing

Found a bug? Have a new use case? Open an Issue.

## License

MIT
