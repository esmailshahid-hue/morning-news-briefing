#!/usr/bin/env python3
"""
Morning Briefing
================
Builds one episode of a private daily news podcast:

  1. Collects the last day's headlines from the feeds listed in config.yaml
  2. Asks a low-cost Gemini model to write a spoken briefing, using ONLY those articles
  3. Turns the script into audio (Google Cloud Chirp 3 HD voice, with Gemini TTS as backup)
  4. Updates the podcast website: MP3 file, feed.xml, and a simple web page

You normally never run this yourself; GitHub Actions runs it every morning.
Useful options when testing:
  python briefing.py --feeds-only   # only check which news feeds work
  python briefing.py --no-audio     # write the script but skip audio and publishing
"""
from __future__ import annotations

import argparse
import base64
import difflib
import html
import io
import json
import logging
import os
import re
import sys
import time
import wave
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("briefing")

USER_AGENT = "Mozilla/5.0 (compatible; MorningBriefingBot/1.0)"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CLOUD_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
SAMPLE_RATE = 24000  # Hz, mono, 16-bit. Both voice engines produce this natively.
UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Errors and HTTP helpers
# --------------------------------------------------------------------------- #
class RetryableError(Exception):
    """A temporary problem (rate limit, server error, network). Worth retrying."""


class PermanentError(Exception):
    """A problem retrying won't fix (bad key, unknown model). Move to the backup."""


def post_json(url: str, body: dict, api_key: str, timeout: int = 180) -> dict:
    try:
        r = requests.post(
            url,
            json=body,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RetryableError(f"network error: {exc}") from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryableError(f"HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code != 200:
        raise PermanentError(f"HTTP {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except ValueError as exc:
        raise RetryableError("response was not valid JSON") from exc


def with_retries(fn, what: str, attempts: int = 3, delay: float = 8.0):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RetryableError as exc:
            last = exc
            log.warning("%s failed (attempt %d/%d): %s", what, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay * attempt)
    raise last


# --------------------------------------------------------------------------- #
# 1. News collection
# --------------------------------------------------------------------------- #
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(text: str | None, limit: int) -> str:
    text = html.unescape(TAG_RE.sub(" ", text or ""))
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def feed_url(feed: dict) -> str:
    if feed.get("url"):
        return feed["url"]
    query = quote_plus(f"{feed['google_news']} when:1d")
    return f"https://news.google.com/rss/search?q={query}&hl=en-PK&gl=PK&ceid=PK:en"


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime(*value[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return None


def fetch_feeds(cfg: dict, now: datetime) -> tuple[list[dict], list[tuple]]:
    news = cfg.get("news", {})
    max_age = timedelta(hours=news.get("max_age_hours", 26))
    per_feed = news.get("per_feed", 8)
    summary_chars = news.get("summary_chars", 350)

    articles: list[dict] = []
    report: list[tuple] = []
    for section in cfg["sections"]:
        for feed in section["feeds"]:
            name = feed["name"]
            is_google = "google_news" in feed and not feed.get("url")
            try:
                resp = requests.get(feed_url(feed), headers={"User-Agent": USER_AGENT}, timeout=25)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
                if not parsed.entries:
                    raise ValueError("feed had no stories")
            except Exception as exc:  # one bad feed must never stop the briefing
                log.warning("Feed %s failed: %s", name, exc)
                report.append((section["name"], name, "FAILED", str(exc)[:120]))
                continue

            kept = 0
            for entry in parsed.entries:
                if kept >= per_feed:
                    break
                title = clean_text(entry.get("title"), 220)
                if not title:
                    continue
                published = entry_time(entry)
                if published and now - published > max_age:
                    continue
                if published and published > now:
                    published = now
                source = name
                summary = clean_text(entry.get("summary") or entry.get("description"), summary_chars)
                if is_google:
                    # Google News titles look like "Headline - Outlet"; its summaries are just links.
                    outlet = (entry.get("source") or {}).get("title")
                    if outlet:
                        source = outlet
                        suffix = f" - {outlet}"
                        if title.endswith(suffix):
                            title = title[: -len(suffix)]
                    summary = ""
                articles.append(
                    {
                        "section": section["name"],
                        "source": source,
                        "kind": feed.get("kind", "news"),
                        "title": title,
                        "summary": summary,
                        "url": entry.get("link", ""),
                        "published": published,
                    }
                )
                kept += 1
            report.append((section["name"], name, "OK", f"{kept} recent stories"))
    return dedupe(articles, [s["name"] for s in cfg["sections"]]), report


def _normalise(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def dedupe(articles: list[dict], section_order: list[str]) -> list[dict]:
    oldest = datetime.min.replace(tzinfo=UTC)
    # Direct feeds before Google News copies, then news before analysis, newest first,
    # so the best copy survives.
    articles.sort(
        key=lambda a: (
            a["summary"] == "",
            a["kind"] != "news",
            -(a["published"] or oldest).timestamp(),
        )
    )
    kept: list[dict] = []
    seen: list[str] = []
    for art in articles:
        norm = _normalise(art["title"])
        if any(difflib.SequenceMatcher(None, norm, other).ratio() > 0.85 for other in seen):
            continue
        seen.append(norm)
        kept.append(art)
    kept.sort(key=lambda a: (section_order.index(a["section"]), -(a["published"] or oldest).timestamp()))
    for i, art in enumerate(kept, start=1):
        art["id"] = i
    return kept


# --------------------------------------------------------------------------- #
# 2. Script writing
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You write and edit a private spoken morning news briefing. It is turned into audio \
and heard in a car, so it must sound natural when read aloud by a newsreader.

ACCURACY RULES (most important):
- Use ONLY the numbered articles provided. Never add facts, names, numbers, dates or events from your \
own knowledge, even if you believe them to be true. If the articles do not cover something, leave it out.
- Only say a number (index level, price, percentage, exchange rate, casualty figure) if it appears in \
the article text you were given. Otherwise describe the direction only if the article states it.
- Some items only have a headline. If a headline alone does not make clear what happened, skip it \
rather than guess.
- Attribute naturally, for example "according to Dawn" or "Business Recorder reports".
- Stay neutral and balanced on political topics. No opinions, no speculation.

ANALYSIS SOURCES: Each article is labelled "news" or "analysis". Factual reporting and \
analysis/commentary are not interchangeable.
- When you use a source marked "analysis", attribute its interpretations, opinions, judgments, \
predictions or causal claims to that source by name. Do not present an analyst's or commentator's \
view as an independently established fact.
- When a "news" article and an "analysis" article cover the same development, use the "news" \
article for the underlying event and use the "analysis" source only for clearly attributed \
interpretation on top of it.
- Never let an "analysis" source override or contradict stronger "news" source material without \
attribution.
- Acceptable phrasing: "Prof G Markets argues that...", "The Economist notes that...", \
"According to The Economist's analysis...". Don't repeat attribution for a plain fact already \
clearly supported by ordinary news sources.

EDITORIAL RULES:
- Choose by importance, not by recency. Merge stories that several outlets cover.
- Start with a one-line greeting that includes the day and date, then a quick "top three" rundown.
- Then cover the sections in the order given, with short spoken transitions such as \
"Turning to technology."
- End with "one thing to watch today" ONLY if an article mentions a scheduled event, then a brief sign-off.
- Aim for about {words} words and never exceed {max_words} words.

WRITING FOR THE EAR:
- Short, clear sentences. Plain text only: no markdown, bullets, headings, emojis, URLs or parentheses.
- Write "percent" not "%", "dollars" not "$", "rupees" not "Rs", "billion" not "bn".
- Separate paragraphs with a blank line.

Return JSON with:
- "title": a short episode title (under 70 characters) naming the top story,
- "script": the full spoken script,
- "stories": the stories you covered, in order, each with a short "headline" and the "article_ids" \
you used for it."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "script": {"type": "STRING"},
        "stories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "headline": {"type": "STRING"},
                    "article_ids": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                },
                "required": ["headline", "article_ids"],
            },
        },
    },
    "required": ["title", "script", "stories"],
}


def age_label(published: datetime | None, now: datetime) -> str:
    if not published:
        return "time unknown"
    hours = int((now - published).total_seconds() // 3600)
    return "under 1h ago" if hours < 1 else f"{hours}h ago"


def build_prompts(cfg: dict, articles: list[dict], now: datetime) -> tuple[str, str]:
    brief = cfg.get("briefing", {})
    words = int(brief.get("target_minutes", 7.5) * brief.get("words_per_minute", 150))
    system = SYSTEM_PROMPT.format(words=words, max_words=int(words * 1.15))

    local = now.astimezone(ZoneInfo(cfg["show"]["timezone"]))
    lines = [
        f"Today is {local.strftime('%A, %d %B %Y')} (local time {local.strftime('%H:%M')}).",
        f"Listener: {cfg['show'].get('listener', '').strip()}",
        "",
        "SECTIONS, in this order:",
    ]
    for sec in cfg["sections"]:
        lines.append(f"- {sec['name']}: {sec.get('focus', '').strip()}")
    lines += ["", "ARTICLES:"]
    for art in articles:
        line = (
            f"[{art['id']}] ({art['section']} | {art['source']} | {art['kind']} | "
            f"{age_label(art['published'], now)}) {art['title']}"
        )
        if art["summary"]:
            line += f" -- {art['summary']}"
        lines.append(line)
    return system, "\n".join(lines)


def parse_script_response(data: dict) -> dict:
    candidates = data.get("candidates") or []
    if not candidates:
        raise PermanentError(f"no output returned (feedback: {data.get('promptFeedback')})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        result = json.loads(text)
    except ValueError as exc:
        raise RetryableError("model did not return valid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("script"), str):
        raise RetryableError("model response was missing the script")
    result.setdefault("title", "")
    result.setdefault("stories", [])
    return result


def write_script(cfg: dict, api_key: str, system: str, user: str) -> tuple[dict, str]:
    ai = cfg.get("ai", {})
    brief = cfg.get("briefing", {})
    min_words = int(brief.get("target_minutes", 7.5) * brief.get("words_per_minute", 150) * 0.5)
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": ai.get("temperature", 0.3),
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    for model in ai["script_models"]:
        url = GEMINI_URL.format(model=model)

        def attempt():
            result = parse_script_response(post_json(url, body, api_key))
            n_words = len(result["script"].split())
            if n_words < min_words:
                raise RetryableError(f"script too short ({n_words} words)")
            return result

        try:
            return with_retries(attempt, f"Script writing with {model}"), model
        except (RetryableError, PermanentError) as exc:
            log.warning("Model %s could not write the script: %s", model, exc)
    raise RuntimeError("Every script model failed. See the warnings above.")


def clean_for_speech(script: str) -> str:
    script = re.sub(r"https?://\S+", "", script)
    script = re.sub(r"[*#_`>|]", "", script)
    script = re.sub(r"[ \t]+", " ", script)
    script = re.sub(r"\n{3,}", "\n\n", script)
    return script.strip()


# --------------------------------------------------------------------------- #
# 3. Audio
# --------------------------------------------------------------------------- #
def _split_long(text: str, max_bytes: int) -> list[str]:
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = [sentence] if len(sentence.encode()) <= max_bytes else sentence.split()
        for word in words:
            joiner = " " if current else ""
            if len((current + joiner + word).encode()) <= max_bytes:
                current += joiner + word
            else:
                if current:
                    pieces.append(current)
                current = word
    if current:
        pieces.append(current)
    return pieces


def split_for_tts(text: str, max_bytes: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        for piece in [para] if len(para.encode()) <= max_bytes else _split_long(para, max_bytes):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate.encode()) <= max_bytes:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def pcm_from_audio(raw: bytes, rate_hint: int = SAMPLE_RATE) -> bytes:
    """Return 16-bit mono PCM at SAMPLE_RATE from WAV bytes or raw PCM."""
    rate, channels, pcm = rate_hint, 1, raw
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw)) as wav:
            if wav.getsampwidth() != 2:
                raise PermanentError("unexpected audio sample width")
            rate, channels = wav.getframerate(), wav.getnchannels()
            pcm = wav.readframes(wav.getnframes())
    if channels != 1 or rate != SAMPLE_RATE:
        import audioop  # available in Python 3.12 (the version the workflow uses)

        if channels != 1:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        if rate != SAMPLE_RATE:
            pcm, _ = audioop.ratecv(pcm, 2, 1, rate, SAMPLE_RATE, None)
    return pcm


def tts_google_cloud(chunk: str, cfg: dict, api_key: str) -> bytes:
    voice = cfg["voice"]["google_cloud"]
    audio_config = {"audioEncoding": "LINEAR16", "sampleRateHertz": SAMPLE_RATE}
    if voice.get("speaking_rate"):
        audio_config["speakingRate"] = voice["speaking_rate"]
    body = {
        "input": {"text": chunk},
        "voice": {"languageCode": voice["language_code"], "name": voice["name"]},
        "audioConfig": audio_config,
    }
    data = post_json(CLOUD_TTS_URL, body, api_key, timeout=240)
    content = data.get("audioContent")
    if not content:
        raise RetryableError("no audio returned")
    return pcm_from_audio(base64.b64decode(content))


def tts_gemini(chunk: str, cfg: dict, api_key: str) -> bytes:
    g = cfg["voice"]["gemini"]
    body = {
        "contents": [{"parts": [{"text": f"{g['style']}\n\n{chunk}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": g["voice"]}}},
        },
    }
    data = post_json(GEMINI_URL.format(model=g["model"]), body, api_key, timeout=300)
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                match = re.search(r"rate=(\d+)", inline.get("mimeType", ""))
                rate = int(match.group(1)) if match else SAMPLE_RATE
                return pcm_from_audio(base64.b64decode(inline["data"]), rate_hint=rate)
    raise RetryableError("no audio returned")


ENGINES = {
    # name: (function, environment variable holding the key, max bytes per request)
    "google_cloud": (tts_google_cloud, "GOOGLE_TTS_API_KEY", 3500),
    "gemini": (tts_gemini, "GEMINI_API_KEY", 1500),
}


def synthesize(script: str, cfg: dict) -> tuple[list[bytes], str]:
    for engine in cfg["voice"]["engines"]:
        fn, key_name, max_bytes = ENGINES[engine]
        key = os.environ.get(key_name, "").strip()
        if not key:
            log.warning("Skipping voice engine %s: %s is not set", engine, key_name)
            continue
        chunks = split_for_tts(script, max_bytes)
        log.info("Voice engine %s: %d chunks", engine, len(chunks))
        try:
            segments = [
                with_retries(lambda c=chunk: fn(c, cfg, key), f"{engine} audio chunk {i}/{len(chunks)}")
                for i, chunk in enumerate(chunks, start=1)
            ]
            return segments, engine
        except (RetryableError, PermanentError, KeyError, ValueError, wave.Error) as exc:
            log.warning("Voice engine %s failed, trying the next one: %s", engine, exc)
    raise RuntimeError("Every voice engine failed. See the warnings above.")


def build_mp3(segments: list[bytes], out_path: Path, bitrate: int) -> int:
    import lameenc

    pause = b"\x00\x00" * int(SAMPLE_RATE * 0.6)
    pcm = pause.join(segments)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    out_path.write_bytes(encoder.encode(pcm) + encoder.flush())
    return int(len(pcm) / 2 / SAMPLE_RATE)


# --------------------------------------------------------------------------- #
# 4. Podcast website
# --------------------------------------------------------------------------- #
def public_base_url() -> str:
    override = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if override:
        return override.rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        owner = owner.lower()
        if name.lower() == f"{owner}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{name}/"
    return "http://localhost:8000/"


def show_notes(result: dict, articles: list[dict], script: str) -> str:
    by_id = {a["id"]: a for a in articles}
    items = []
    for story in result.get("stories", []):
        links = []
        for aid in story.get("article_ids", []):
            art = by_id.get(aid) if isinstance(aid, int) else None
            if art and art["url"].startswith("http"):
                links.append(f'<a href="{html.escape(art["url"])}">{html.escape(art["source"])}</a>')
        headline = html.escape(story.get("headline", ""))
        items.append(f"<li>{headline}{' — ' + ', '.join(links) if links else ''}</li>")
    paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in script.split("\n\n") if p.strip())
    notes = f"<p><strong>Stories</strong></p><ul>{''.join(items)}</ul><p><strong>Transcript</strong></p>{paragraphs}"
    return notes.replace("]]>", "]] >")


def fmt_duration(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def write_feed(site: Path, cfg: dict, episodes: list[dict], base: str) -> None:
    show = cfg["show"]
    last_build = format_datetime(datetime.now(UTC))
    items = []
    for ep in episodes:
        items.append(
            f"""    <item>
      <title>{xml_escape(ep['title'])}</title>
      <guid isPermaLink="false">{xml_escape(ep['guid'])}</guid>
      <pubDate>{format_datetime(datetime.fromisoformat(ep['published']))}</pubDate>
      <enclosure url="{xml_escape(base + ep['file'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{fmt_duration(ep['duration'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <description><![CDATA[{ep['notes']}]]></description>
    </item>"""
        )
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{xml_escape(show['title'])}</title>
    <link>{xml_escape(base)}</link>
    <atom:link href="{xml_escape(base + 'feed.xml')}" rel="self" type="application/rss+xml"/>
    <description>{xml_escape(show['description'])}</description>
    <language>{xml_escape(show.get('language', 'en'))}</language>
    <lastBuildDate>{last_build}</lastBuildDate>
    <itunes:author>{xml_escape(show.get('author', show['title']))}</itunes:author>
    <itunes:image href="{xml_escape(base + 'cover.png')}"/>
    <image><url>{xml_escape(base + 'cover.png')}</url><title>{xml_escape(show['title'])}</title><link>{xml_escape(base)}</link></image>
    <itunes:category text="News"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (site / "feed.xml").write_text(feed, encoding="utf-8")


def write_index(site: Path, cfg: dict, episodes: list[dict], base: str) -> None:
    title = html.escape(cfg["show"]["title"])
    rows = "".join(
        f"<article><h2>{html.escape(ep['title'])}</h2><p class='meta'>{html.escape(ep['label'])} · "
        f"{ep['duration'] // 60} min</p><audio controls preload='none' src='{html.escape(ep['file'])}'></audio></article>"
        for ep in episodes
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title}</title>
<style>
:root{{--bg:#f6f4ef;--fg:#1c1f26;--muted:#5d6472;--card:#fff;--line:#e3dfd6;--accent:#1f4e79}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12151b;--fg:#e8e6e1;--muted:#9aa1ad;--card:#1b2029;--line:#2a303b;--accent:#8fb8e0}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:640px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:1.6rem;margin:0 0 4px}} h2{{font-size:1.05rem;margin:0 0 4px}}
.feed{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:16px 0 24px;word-break:break-all}}
.feed code{{color:var(--accent)}} article{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}}
.meta{{color:var(--muted);margin:0 0 8px;font-size:.9rem}} audio{{width:100%}}
</style></head><body><main>
<h1>{title}</h1><p class="meta">Private daily briefing. Add this feed to Apple Podcasts (Library › ⋯ › Follow a Show by URL):</p>
<div class="feed"><code>{html.escape(base)}feed.xml</code></div>
{rows or '<p>No episodes yet.</p>'}
</main></body></html>
"""
    (site / "index.html").write_text(page, encoding="utf-8")


def publish(site: Path, cfg: dict, episode: dict, mp3_tmp: Path) -> list[dict]:
    base = public_base_url()
    ep_dir = site / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    index_file = site / "episodes.json"
    episodes = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else []

    episodes = [e for e in episodes if e.get("date") != episode["date"]]  # a re-run replaces today's episode
    mp3_tmp.replace(site / episode["file"])
    episodes.insert(0, episode)
    episodes.sort(key=lambda e: e["published"], reverse=True)
    episodes = episodes[: cfg["show"].get("keep_episodes", 7)]

    wanted = {Path(e["file"]).name for e in episodes}
    for old in ep_dir.glob("*.mp3"):
        if old.name not in wanted:
            old.unlink()

    index_file.write_text(json.dumps(episodes, indent=2), encoding="utf-8")
    write_feed(site, cfg, episodes, base)
    write_index(site, cfg, episodes, base)
    (site / ".nojekyll").write_text("", encoding="utf-8")
    cover = ROOT / "assets" / "cover.png"
    if cover.exists():
        (site / "cover.png").write_bytes(cover.read_bytes())
    log.info("Feed URL: %sfeed.xml", base)
    return episodes


# --------------------------------------------------------------------------- #
# Run report (shown on the GitHub Actions run page)
# --------------------------------------------------------------------------- #
def write_summary(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)


def feed_table(report: list[tuple]) -> list[str]:
    lines = ["| Section | Feed | Status | Detail |", "|---|---|---|---|"]
    for section, name, status, detail in report:
        icon = "✅" if status == "OK" else "⚠️"
        lines.append(f"| {section} | {name} | {icon} {status} | {detail.replace('|', '/')} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Build today's morning briefing episode.")
    parser.add_argument("--site-dir", default="site", help="folder holding the podcast website")
    parser.add_argument("--feeds-only", action="store_true", help="only test the news feeds")
    parser.add_argument("--no-audio", action="store_true", help="write the script, skip audio and publishing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    summary = [f"# {cfg['show']['title']}", ""]
    report: list[tuple] = []
    try:
        articles, report = fetch_feeds(cfg, now)
        summary.append(f"**Stories collected:** {len(articles)}")
        if args.feeds_only:
            write_summary(summary + [""] + feed_table(report))
            return 0

        minimum = cfg.get("news", {}).get("min_articles", 15)
        if len(articles) < minimum:
            raise RuntimeError(f"Only {len(articles)} stories found (need {minimum}). News feeds may be down.")

        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it under Settings › Secrets and variables › Actions.")

        system, user = build_prompts(cfg, articles, now)
        result, model = write_script(cfg, gemini_key, system, user)
        script = clean_for_speech(result["script"])
        summary.append(f"**Script:** {len(script.split())} words, written by `{model}`")
        if args.no_audio:
            write_summary(summary + ["", "## Script", "", script, ""] + feed_table(report))
            return 0

        segments, engine = synthesize(script, cfg)
        local = now.astimezone(ZoneInfo(cfg["show"]["timezone"]))
        date = local.strftime("%Y-%m-%d")
        filename = f"episodes/{date}-{local.strftime('%H%M')}.mp3"
        tmp = Path(args.site_dir).parent / "episode.tmp.mp3"
        duration = build_mp3(segments, tmp, cfg["voice"].get("mp3_bitrate_kbps", 48))
        if duration < 60:
            raise RuntimeError(f"Audio is only {duration} seconds long; something went wrong.")

        label = local.strftime("%a %d %b %Y")
        top = (result.get("title") or "").strip()
        episode = {
            "date": date,
            "guid": f"{date}-{int(now.timestamp())}",
            "title": f"{label} · {top}" if top else label,
            "label": label,
            "file": filename,
            "bytes": tmp.stat().st_size,
            "duration": duration,
            "published": now.isoformat(),
            "notes": show_notes(result, articles, script),
        }
        site = Path(args.site_dir)
        site.mkdir(parents=True, exist_ok=True)
        publish(site, cfg, episode, tmp)
        summary += [
            f"**Voice:** `{engine}` · **Length:** {duration // 60} min {duration % 60} s",
            f"**Feed:** {public_base_url()}feed.xml",
            "",
        ]
        write_summary(summary + feed_table(report))
        return 0
    except Exception as exc:
        log.exception("Briefing failed")
        write_summary(summary + ["", "## ❌ Briefing failed", "", str(exc), ""] + (feed_table(report) if report else []))
        return 1


if __name__ == "__main__":
    sys.exit(main())
