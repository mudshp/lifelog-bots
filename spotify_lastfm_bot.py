"""
Spotify Life Log Bot (via Last.fm)
----------------------------------
毎朝 7:00: 昨日のトップ5曲 + 総再生数を Discord に投下
毎週月曜 7:00: 先週のトップアーティスト/トラックを投下

使い方:
  python spotify_lastfm_bot.py daily     # 日次レポート
  python spotify_lastfm_bot.py weekly    # 週次レポート

cron 例 (毎朝 7:00 に daily、毎週月曜 7:05 に weekly):
  0 7 * * *   /usr/bin/python3 /path/to/spotify_lastfm_bot.py daily
  5 7 * * 1   /usr/bin/python3 /path/to/spotify_lastfm_bot.py weekly
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_USER = os.getenv("LASTFM_USER")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# JST で扱う (日本時間で "昨日" / "先週" を計算)
JST = timezone(timedelta(hours=9))

LASTFM_API = "https://ws.audioscrobbler.com/2.0/"


# ------------------------------------------------------------
# Last.fm API
# ------------------------------------------------------------
def fetch_recent_tracks(from_ts: int, to_ts: int) -> list[dict]:
    """指定期間に再生されたトラック一覧を取得 (ページネーション対応)"""
    tracks = []
    page = 1
    while True:
        params = {
            "method": "user.getrecenttracks",
            "user": LASTFM_USER,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "from": from_ts,
            "to": to_ts,
            "limit": 200,
            "page": page,
        }
        r = requests.get(LASTFM_API, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        recent = data.get("recenttracks", {})
        page_tracks = recent.get("track", [])
        if isinstance(page_tracks, dict):
            page_tracks = [page_tracks]

        # "nowplaying" は再生途中なので除外
        for t in page_tracks:
            attr = t.get("@attr", {})
            if attr.get("nowplaying") == "true":
                continue
            tracks.append(t)

        total_pages = int(recent.get("@attr", {}).get("totalPages", 1))
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.25)  # レート制限対策

    return tracks


# ------------------------------------------------------------
# 集計
# ------------------------------------------------------------
def aggregate_top_tracks(tracks: list[dict], top_n: int = 5) -> list[tuple[str, int]]:
    """(アーティスト - 曲名) ごとの再生回数ランキング"""
    counter = Counter()
    for t in tracks:
        name = t.get("name", "").strip()
        artist = t.get("artist", {}).get("#text", "").strip()
        if name and artist:
            counter[f"{artist} — {name}"] += 1
    return counter.most_common(top_n)


def aggregate_top_artists(tracks: list[dict], top_n: int = 5) -> list[tuple[str, int]]:
    counter = Counter()
    for t in tracks:
        artist = t.get("artist", {}).get("#text", "").strip()
        if artist:
            counter[artist] += 1
    return counter.most_common(top_n)


# ------------------------------------------------------------
# Discord 投下
# ------------------------------------------------------------
def post_to_discord(embed: dict) -> None:
    payload = {"embeds": [embed]}
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()


def build_daily_embed(tracks: list[dict], target_date: datetime) -> dict:
    top_tracks = aggregate_top_tracks(tracks, top_n=5)
    total_plays = len(tracks)
    unique_artists = len({t.get("artist", {}).get("#text", "") for t in tracks})

    if top_tracks:
        top_lines = "\n".join(
            f"`{i+1}.` **{name}** — {count}回"
            for i, (name, count) in enumerate(top_tracks)
        )
    else:
        top_lines = "再生履歴なし 🌙"

    description = (
        f"🎧 **{total_plays}曲** 再生 / {unique_artists}アーティスト\n\n"
        f"**Top 5**\n{top_lines}"
    )

    return {
        "title": f"🎵 昨日の音楽ログ — {target_date.strftime('%Y/%m/%d (%a)')}",
        "description": description,
        "color": 0x1DB954,  # Spotify green
        "footer": {"text": f"via Last.fm ({LASTFM_USER})"},
    }


def build_weekly_embed(tracks: list[dict], start: datetime, end: datetime) -> dict:
    top_tracks = aggregate_top_tracks(tracks, top_n=10)
    top_artists = aggregate_top_artists(tracks, top_n=10)
    total_plays = len(tracks)

    def fmt_lines(rows):
        return "\n".join(
            f"`{i+1:>2}.` {name} — **{count}**"
            for i, (name, count) in enumerate(rows)
        ) or "なし"

    description = (
        f"🎧 今週の総再生数: **{total_plays}曲**\n\n"
        f"**🏆 Top Artists**\n{fmt_lines(top_artists)}\n\n"
        f"**🎶 Top Tracks**\n{fmt_lines(top_tracks)}"
    )

    period = f"{start.strftime('%m/%d')} 〜 {end.strftime('%m/%d')}"
    return {
        "title": f"📊 週次レポート — {period}",
        "description": description,
        "color": 0x1DB954,
        "footer": {"text": f"via Last.fm ({LASTFM_USER})"},
    }


# ------------------------------------------------------------
# メインロジック
# ------------------------------------------------------------
def run_daily() -> None:
    now = datetime.now(JST)
    yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today = yesterday + timedelta(days=1)

    tracks = fetch_recent_tracks(int(yesterday.timestamp()), int(today.timestamp()))
    embed = build_daily_embed(tracks, yesterday)
    post_to_discord(embed)
    print(f"[daily] {len(tracks)} tracks posted.")


def run_weekly() -> None:
    now = datetime.now(JST)
    # 先週の月曜 0:00 〜 日曜 23:59 (今日が月曜の想定)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_monday = this_monday - timedelta(days=7)

    tracks = fetch_recent_tracks(int(last_monday.timestamp()), int(this_monday.timestamp()))
    embed = build_weekly_embed(tracks, last_monday, this_monday - timedelta(seconds=1))
    post_to_discord(embed)
    print(f"[weekly] {len(tracks)} tracks posted.")


def main() -> None:
    if not all([LASTFM_API_KEY, LASTFM_USER, DISCORD_WEBHOOK_URL]):
        print("ERROR: .env に LASTFM_API_KEY / LASTFM_USER / DISCORD_WEBHOOK_URL を設定してください。")
        sys.exit(1)

    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if mode == "daily":
        run_daily()
    elif mode == "weekly":
        run_weekly()
    else:
        print(f"Unknown mode: {mode}. Use 'daily' or 'weekly'.")
        sys.exit(1)


if __name__ == "__main__":
    main()