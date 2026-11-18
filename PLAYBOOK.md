# Posting & ops playbook

**What this is for:** paid clipping campaigns (Whop Content Rewards: about $1 per 1,000 approved views, capped per clip). The target is 20–30 clips a day across platforms (about 8–10 per account), posted from your own accounts, with every link submitted to its campaign right away.

**Platforms:** TikTok (main payout), YouTube Shorts (long tail), Instagram Reels. X only if a campaign allows it. **Never post the same clip twice on the same platform.** `schedule` and `track add` both enforce this.

## Launch timing

The first 24–48 hours after the **Nov 19** unlock are the gold window. If the full pipeline isn't ready, run the MVP and caption by hand:

```bash
uv run clipper run <vod_url> --streamer <name> --top 15 --no-llm
```

## Daily loop

1. **Check** which campaign streamers are live or have new VODs.
2. **Clip:**
   ```bash
   uv run clipper run <vod_url> --streamer <name> --top 15 [--start 1h --end 4h] [--chat chat.json]
   ```
   Review `out/<streamer>/<job>/clips.csv` best-first. Delete weak clips and fix any in CapCut.
3. **Schedule:**
   ```bash
   uv run clipper schedule <job_id> --accounts tiktok,youtube,instagram --start 18:00 --gap 45m
   ```
   This writes `schedule.csv` with platform, time, and the platform-specific title and caption. Shorts get a title of 100 chars or fewer ending in `#Shorts #GTA6`; TikTok and Reels get caption plus hashtags. The campaign's required tags and mentions are already in the caption.
4. **Post** 30–60 min apart per account. Don't dump 10 at once. Use the generated title and caption plus anything else the campaign requires.
5. **Submit** every posted link to its campaign immediately, then log it:
   ```bash
   uv run clipper track add out/<streamer>/<job>/clip_03_x.mp4 --platform tiktok --url <post_url>
   ```
6. **Next day:** update views, then double down on what's hitting (streamer, category, hook style):
   ```bash
   uv run clipper track views <post_url> 48210
   uv run clipper track report       # views + est. $ per streamer / category / platform; flags unsubmitted links
   ```

## Campaign rules in config.yaml

```yaml
campaigns:
  - name: kai-gta6-whop
    streamers: [kaicenat]
    platforms: [tiktok, youtube, instagram]
    required_tags: ["#ad", "@kaicenat"]
    min_len: 15
    max_len: 60
    rate_per_1k: 1.0
    cap_per_clip: 100
```

Once any campaign is configured:
- `run` refuses streamers that aren't in a campaign.
- Clip lengths follow the campaign's `min_len` / `max_len`.
- Missing required tags are appended to every caption.
- `schedule` skips platforms the campaign doesn't pay for.
- `track` estimates earnings as `views / 1000 * rate_per_1k`, capped at `cap_per_clip`.

## My checklist

- [ ] TikTok, YouTube, Instagram accounts under one niche name; warmed up by scrolling GTA content
- [ ] Joined 3–5 GTA/streamer campaigns on Whop; noted each one's rules (→ `campaigns:` in config.yaml)
- [ ] ffmpeg, uv, gh installed; `gh auth login`; Anthropic API key in `.env`; optional Twitch dev app
- [ ] 100GB+ free disk (1h of 1080p60 VOD ≈ 5–10GB)
- [ ] CapCut installed for manual touch-ups
- [ ] (Twitch chat) TwitchDownloaderCLI installed for `--chat` exports
