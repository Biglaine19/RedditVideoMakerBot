# TikTok channel kit

![profile pic](profile-pic.jpg)

## Name
- **Display name:** Night Owl Stories
- **Handle:** `@nightowlstories` (backups: `@nightowl.reads`, `@thenightowlreads`, `@hootstories`)

## Bio (80 characters max)
```
🦉 Reddit's wildest stories, read out loud
New story every night 🌙 Follow for part 2
```

## Content plan
- **Subreddits that perform on TikTok:** AmItheAsshole, AskReddit, TrueOffMyChest,
  pettyrevenge, MaliciousCompliance, entitledparents, tifu. Set several at once in
  `config.toml`: `subreddit = "AmItheAsshole+pettyrevenge+MaliciousCompliance"`.
- **Length:** 45–75s. Long stories: split into Part 1 / Part 2 (drives follows).
- **Hook:** the first 2 seconds are the post title. Pick titles with a conflict or a question.
- **Background:** `minecraft` or `csgo-surf` (parkour/surf keeps eyes on screen).
- **Voice:** `tiktok` TTS, `en_us_006` or `en_us_002`.
- **Posting:** 1–3 a day. Start with 12:00 and 19:00 local, then let `analyze.py` decide.
- **Caption template:** `Would you have done the same? 👀 #redditstories #reddit #aita #storytime #fyp`
- **Engage:** pin a comment asking "NTA or YTA?", reply to early comments within the first hour.

## Analytics loop
1. Post a video. After 48h, open TikTok Studio → Analytics → the video.
2. Add a row to `stats.csv` (views, likes, comments, shares, average watch time).
3. Run `python tiktok/analyze.py` — it ranks subreddits, posting hours and video lengths by
   average views, engagement and watch-through, and tells you what to do more of.
4. Update `config.toml` to match, generate the next batch (`python main.py`).

The bot itself now ranks candidate threads by engagement velocity (`viral_score` in
`utils/subreddit.py`), so it picks posts that are blowing up right now, not just the top of hot.

## Rules to stay safe
- Reddit content is reposted: credit "r/<subreddit>" in the caption.
- Turn on TikTok's "AI-generated content" label (the voice is synthetic).
- Keep `allow_nsfw = false`; NSFW threads get videos suppressed or the account banned.
