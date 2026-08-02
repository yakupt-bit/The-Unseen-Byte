"""
YouTube Data API ile nişe yakın, son dönemde yüksek izlenen videoları
çeker; başlık kalıplarını ve performans verisini trend.json'a kaydeder.
Bu dosya sonra generate_script.py ve generate_titles.py tarafından
"şu an gerçekten işe yarayan formatlar" referansı olarak kullanılır.

SORGU ÖNCELİK SIRASI:
  1. --query elle verildiyse onu kullan (en yüksek öncelik).
  2. Verilmediyse, workflow'un "0) Pick this run's niche" adımının
     ayarladığı TOPIC_HINT ortam değişkenine bak - bu, o koşuda hangi
     alt-niş seçildiyse (ör. "Esports & Competitive Culture") onun adını
     içerir, arama sorgusu olarak kullanılır. Böylece trend verisi
     GERÇEKTEN o videonun konusuyla alakalı örnekler getirir.
  3. TOPIC_HINT de yoksa (ör. elle/manuel çalıştırma), aşağıdaki
     QUERY_POOL'dan RASTGELE biri seçilir.

Not: YouTube Data API günlük kota sınırlıdır (varsayılan 10.000 birim/gün,
search.list çağrısı 100 birim tutar) - bu scripti günde birkaç kez
çağırman kotayı hızla tüketebilir, haftalık çalıştırman yeterli.

Kullanım:
    python scripts/trend_analysis.py --out trend.json
    python scripts/trend_analysis.py --query "gaming psychology facts" --out trend.json
"""
import argparse
import datetime
import json
import os
import random

import requests

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Sadece TOPIC_HINT de yoksa (elle/manuel çalıştırma) devreye giren
# yedek havuz - 6 ana kategoriyi temsilen genişletildi.
QUERY_POOL = [
    "gaming psychology facts documentary",
    "video game history mystery",
    "tech facts you didn't know",
    "hidden history technology",
    "gaming industry secrets explained",
    "science of video games",
    "retro gaming untold story",
    "why games are addictive science",
    "esports documentary secrets",
    "lost cancelled video games",
    "video game preservation history",
    "corporate gaming industry decisions",
]


def derive_query_from_topic_hint(topic_hint: str) -> str:
    """TOPIC_HINT formatı 'Kategori Adı - açıklama...' şeklinde
    (research.py'deki SUB_NICHES listesine bakılırsa). Arama sorgusu
    için sadece '-' öncesindeki kısa kategori adını alıyoruz, tam
    açıklamayı değil (YouTube arama API'si uzun cümlelerde daha kötü
    sonuç veriyor)."""
    short_name = topic_hint.split(" - ")[0].strip()
    return short_name


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
    parser.add_argument("--query", required=False, default="",
                         help="Niş ile ilgili arama terimi (boşsa TOPIC_HINT, o da yoksa havuzdan rastgele seçilir)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.query.strip():
        query = args.query.strip()
        source = "elle verildi (--query)"
    else:
        topic_hint = os.environ.get("TOPIC_HINT", "").strip()
        if topic_hint:
            query = derive_query_from_topic_hint(topic_hint) + " documentary"
            source = f"TOPIC_HINT'ten türetildi (\"{topic_hint}\")"
        else:
            query = random.choice(QUERY_POOL)
            source = "TOPIC_HINT yok, havuzdan rastgele seçildi"

    print(f"Kullanılan sorgu: \"{query}\" ({source})")

    api_key = os.environ["YOUTUBE_API_KEY"]
    video_ids = search_recent_popular(query, api_key)
    trend_data = fetch_stats(video_ids, api_key)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trend_data, f, ensure_ascii=False, indent=2)

    print(f"Trend analizi tamamlandı -> {args.out} ({len(trend_data)} video)")
    for v in trend_data[:5]:
        print(f"  {v['views']:>10,} izlenme - {v['title']}")


if __name__ == "__main__":
    main()
