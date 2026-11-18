# gta6-auto-clipper

Twitch/Kick GTA 6 VOD → hype moments (chat spikes + audio spikes + Claude judge) → ready-to-post 9:16 Shorts with burned-in word-by-word captions.

```
VOD ─► ingest (yt-dlp, chat) ─► signals (chat z-score, loudness z-score, Twitch clips) ─► fuse + peaks
    ─► whisper transcript ─► Claude: score/category/trim/hook/title/caption ─► render 1080x1920 ─► manifest.json / clips.csv
```

## Setup

```bash
brew install uv ffmpeg gh        # or apt install ffmpeg; uv: https://docs.astral.sh/uv/
uv sync
cp .env.example .env             # add ANTHROPIC_API_KEY (optional: TWITCH_CLIENT_ID/SECRET)
cp config.example.yaml config.yaml
uv run pytest -q
```

- **ffmpeg** must be on PATH (checked at startup). Captions need libass; if your ffmpeg lacks it (Homebrew's current bottle does), the bundled `imageio-ffmpeg` build is used automatically for rendering.
- First render downloads the whisper model (`small` ≈ 500 MB).
- Disk: 1 h of 1080p60 VOD ≈ 5–10 GB. Use `--start/--end` to grab just part of a VOD.

## Usage

```bash
# all-in-one
uv run clipper run https://www.twitch.tv/videos/123456789 --streamer kaicenat --start 1h --end 3h --top 10
uv run clipper run https://www.twitch.tv/videos/123456789 --top 10 --no-llm      # no API key needed

# step by step
uv run clipper ingest <vod_url> [--start 2h --end 3h] [--chat chat.json]   # prints the job id
uv run clipper analyze <job_id> [--no-llm]     # -> data/<job_id>/candidates.json
uv run clipper render <job_id> --top 10        # -> out/<streamer>/<job_id>/clip_01_<slug>.mp4 ...
uv run clipper calibrate <job_id> --at 00:12:30

# local files
uv run clipper run --video stream.mp4 --chat chat.json --streamer kaicenat
```

The job id is the VOD id, plus `_<start>-<end>` when you pass `--start/--end`. Everything is cached under `data/<job_id>/` (video.mp4, audio.wav at 16 kHz mono, chat.jsonl, transcripts/), so re-runs skip what's already done. Already-rendered clip files are skipped too. Delete a clip to re-render it.

Output per job: ranked `clip_NN_<slug>.mp4` files plus `manifest.json` and `clips.csv`, with rank, VOD timestamp and link, scores, category, title, caption and hook.

### Chat replay

Chat is optional. Without it, the tool runs audio-only (plus Twitch clips if configured).

- Twitch: `chat-downloader` is tried first. As of Nov 2026 Twitch blocks its requests, so the most reliable route is to export chat with [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader) and pass it in:
  ```bash
  TwitchDownloaderCLI chatdownload --id 123456789 -o chat.json
  uv run clipper run https://www.twitch.tv/videos/123456789 --chat chat.json --start 1h --end 2h
  ```
  The full-VOD export is trimmed to your `--start/--end` window automatically.
- `--chat` accepts TwitchDownloader JSON, chat-downloader JSON, or JSONL lines of `{"t": sec, "author": "...", "text": "..."}`.
- Kick: best-effort. If chat can't be fetched, you get a warning and the run continues audio-only.

## Facecam calibration

1. `uv run clipper calibrate <job_id> --at 00:12:30` writes `data/<job_id>/calibrate_<t>.png` and prints the source size.
2. Open the PNG in Preview (Tools → Show Inspector shows the selection size) and read off the facecam rectangle in source pixels.
3. Add it to `config.yaml`:
   ```yaml
   streamers:
     penta:
       facecam: { x: 1428, y: 778, w: 492, h: 302 }
   ```
   With a facecam set, the layout is facecam in the top 35% and center-cropped gameplay in the bottom 65%, with captions on the seam. Without one, the full frame sits on a blurred, zoomed background.

## Tuning

| Knob | Effect |
|---|---|
| `weights.chat / audio / clips` | How much each signal counts. Raise `audio` for streamers with dead chat, and `clips` when Twitch credentials are set (viewer clips are the strongest signal). |
| `chat_lag_seconds` | Shifts the chat signal earlier. If clips start too late, raise it. |
| `min_score` | Lower it for more candidates from quiet VODs. |
| `min_gap_seconds` | Minimum spacing between clips. |
| `max_candidates` | How many peaks get sent to the judge. Haiku is cheap, so 25–40 is fine. |
| `min_llm_score` | Quality bar: 6 is permissive, 7–8 is strict. |
| `keywords` | Emote and phrase weights. Add streamer-specific emotes. |

Ranking: `final = 0.6 * llm_score/10 + 0.4 * fused/max_fused`, or the fused score alone with `--no-llm`. Clip windows run from 25 s before the peak to 10 s after, with both edges snapped to the quietest nearby second and the length forced into 15–60 s (or the campaign's limits). Claude then trims within that window.

## Posting helpers

See [PLAYBOOK.md](PLAYBOOK.md) for the daily loop.

```bash
uv run clipper schedule <job_id> --accounts tiktok,youtube,instagram --start 18:00 --gap 45m   # -> schedule.csv
uv run clipper track add out/kaicenat/<job>/clip_01_x.mp4 --platform tiktok --url <post_url>
uv run clipper track views <post_url> 48210
uv run clipper track report
```

## Phase 2 (planned, not built)

- `clipper live <channel>` (stub today): record live in 5-min segments with `streamlink`, read chat live over anonymous Twitch IRC (`justinfan` login, no auth), run the same detector on a rolling buffer, and render clips within minutes of the moment.
- SQLite tracking of posted clips plus views and earnings per clip, then learn which categories and hooks perform and retune `weights` / `min_llm_score` per streamer. `tracker.csv` is the MVP version.
