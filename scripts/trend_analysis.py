"""
YouTube Data API ile nişe yakın, son dönemde yüksek izlenen videoları
çeker; başlık kalıplarını ve performans verisini trend.json'a kaydeder.
Bu dosya sonra generate_script.py ve generate_titles.py tarafından
"şu an gerçekten işe yarayan formatlar" referansı olarak kullanılır.

Not: YouTube Data API günlük kota sınırlıdır (varsayılan 10.000 birim/gün,
search.list çağrısı 100 birim tutar) — bu scripti günde birkaç kez
çağırman kotayı hızla tüketebilir, haftalık çalıştırman yeterli.

Kullanım:
    python scripts/trend_analysis.py --query "gaming psychology facts" --out trend.json
"""
import argparse
import datetime
import os

import requests

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def search_recent_popular(query: str, api_key: str, days_back: int = 60, max_results: int = 15):
    published_after = (
        datetime.datetime.utcnow() - datetime.timedelta(days=days_back)
    ).isoformat("T") + "Z"

    resp = requests.get(SEARCH_URL, params={
        "key": api_key,
        "part": "snippet",
        "type": "video",
        "q": query,
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": max_results,
        "relevanceLanguage": "en",
    }, timeout=30)
    resp.raise_for_status()
    return [item["id"]["videoId"] for item in resp.json().get("items", [])]


def fetch_stats(video_ids, api_key):
    if not video_ids:
        return []
    resp = requests.get(VIDEOS_URL, params={
        "key": api_key,
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(video_ids),
    }, timeout=30)
    resp.raise_for_status()
    results = []
    for item in resp.json().get("items", []):
        results.append({
            "title": item["snippet"]["title"],
            "channel": item["snippet"]["channelTitle"],
            "published_at": item["snippet"]["publishedAt"],
            "views": int(item["statistics"].get("viewCount", 0)),
            "likes": int(item["statistics"].get("likeCount", 0)),
            "duration": item["contentDetails"]["duration"],
        })
    return sorted(results, key=lambda x: x["views"], reverse=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True, help="Niş ile ilgili arama terimi")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    api_key = os.environ["YOUTUBE_API_KEY"]

    video_ids = search_recent_popular(args.query, api_key)
    trend_data = fetch_stats(video_ids, api_key)

    import json
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trend_data, f, ensure_ascii=False, indent=2)

    print(f"Trend analizi tamamlandı -> {args.out} ({len(trend_data)} video)")
    for v in trend_data[:5]:
        print(f"  {v['views']:>10,} izlenme — {v['title']}")


if __name__ == "__main__":
    main()
