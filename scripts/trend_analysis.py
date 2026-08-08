"""
YouTube Data API ile, o koşunun konusuna göre Claude'un ürettiği
arama terimleriyle, SON 30 GÜN içinde yayınlanmış ve 250.000+ izlenme
almış videoları çeker; başlık kalıplarını ve performans verisini
trend.json'a kaydeder. Bu dosya sonra generate_script.py ve
generate_thumbnail.py tarafından "şu an gerçekten patlayan formatlar"
referansı olarak kullanılır.

NEDEN SABİT KANAL LİSTESİ DEĞİL: Sabit bir rakip kanal listesi zamanla
hep aynı sonuçları getirir (tekrar riski). Bunun yerine, HER KOŞUDA
Claude o videonun konusuna özel 1-3 arama terimi üretir, bu terimlerle
YouTube'da GERÇEKTEN TAZE (son 30 gün) ve GERÇEKTEN BÜYÜK (250k+
izlenme) videolar aranır - hem tekrar riski yok hem de her zaman güncel.

NEDEN "SON 30 GÜN" (AY BAŞI DEĞİL): Ayın başından itibaren saymak,
ayın erken günlerinde (örn. ayın 2'si) neredeyse hiç video bulunamaması
riskini taşır. Her zaman geriye dönük 30 günlük hareketli bir pencere
kullanmak, hangi güne denk gelirsek gelelim tutarlı sonuç verir.

GÜVENLİK AĞI: Yüksek-performans araması hiç sonuç getirmezse (küçük/niş
bir konu için son 30 günde yeterince büyük video olmayabilir), otomatik
olarak eski/genel niş aramasına (60 günlük, eşiksiz) düşülür -
trend.json asla boş kalmaz, pipeline asla kırılmaz.

SORGU ÖNCELİK SIRASI:
  1. --query elle verildiyse, DOĞRUDAN genel niş araması yapılır
     (yüksek-performans mantığı atlanır).
  2. Verilmediyse, TOPIC_HINT üzerinden Claude'a arama terimleri
     ürettirilip son-30-gün yüksek-performans araması denenir.
  3. O da sonuç vermezse, TOPIC_HINT ya da QUERY_POOL ile genel niş
     araması yapılır (eski davranış, yedek).

Not: YouTube Data API günlük kota sınırlıdır (varsayılan 10.000 birim/gün,
search.list çağrısı 100 birim tutar). Bu script çalışma başına en fazla
~4 search.list çağrısı yapar (1-3 anahtar kelime + olası yedek arama),
güvenli aralıkta kalır.

Kullanım:
    python scripts/trend_analysis.py --out trend.json
    python scripts/trend_analysis.py --query "gaming psychology facts" --out trend.json
"""
import argparse
import datetime
import json
import os
import random

import anthropic
import requests

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

MIN_VIEWS_THIS_MONTH = 250_000
MAX_KEYWORDS = 3
RESULTS_PER_KEYWORD = 15

# Sadece Claude anahtar kelime üretemezse ya da TOPIC_HINT hiç yoksa
# (elle/manuel çalıştırma) devreye giren yedek havuz.
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


def generate_search_keywords(topic_hint: str, client) -> list:
    """Konuya özel, YouTube'da arama yapmaya uygun 1-3 kısa terim
    üretir (Claude Haiku ile, ucuz). Başarısız olursa boş liste döner
    - çağıran taraf eski/genel yönteme düşer, pipeline kırılmaz."""
    if not topic_hint:
        return []
    try:
        prompt = f"""Bu YouTube video konusuna göre, YouTube arama
kutusunda kullanılacak 1 ila 3 arasında KISA (2-4 kelime), spesifik
arama terimi üret. Amaç: bu konuyla GERÇEKTEN alakalı, son 30 günde
YouTube'da yayınlanmış popüler videoları bulmak.

KONU: {topic_hint}

Çıktı SADECE JSON dizi (İngilizce terimler): ["terim 1", "terim 2"]"""
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        keywords = json.loads(cleaned)
        if isinstance(keywords, list) and keywords:
            return [str(k).strip() for k in keywords if str(k).strip()][:MAX_KEYWORDS]
    except Exception as e:
        print(f"  UYARI: arama terimi üretimi başarısız ({type(e).__name__})")
    return []


def last_30_days_iso() -> str:
    """Ayın kaçı olduğuna bakmadan, HER ZAMAN son 30 günü kapsayan
    hareketli bir pencere döndürür - ayın erken günlerinde neredeyse
    hiç video bulunamaması riskini ortadan kaldırır."""
    start = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    return start.isoformat("T") + "Z"


def search_video_ids(query: str, api_key: str, published_after: str = None,
                      max_results: int = 15) -> list:
    """Bir sorgu için video ID listesi döner (istatistik içermez).
    Hata durumunda boş liste döner."""
    params = {
        "key": api_key,
        "part": "snippet",
        "type": "video",
        "q": query,
        "order": "viewCount",
        "maxResults": max_results,
        "relevanceLanguage": "en",
    }
    if published_after:
        params["publishedAfter"] = published_after
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        return [item["id"]["videoId"] for item in resp.json().get("items", [])]
    except requests.RequestException as e:
        print(f"  UYARI: arama başarısız ({type(e).__name__}) - sorgu: \"{query}\"")
        return []


def fetch_stats(video_ids: list, api_key: str, source_label: str) -> list:
    """Video ID listesinden gerçek istatistikleri çeker, her kayda
    'source' etiketi ekler."""
    if not video_ids:
        return []
    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = requests.get(VIDEOS_URL, params={
                "key": api_key,
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(batch),
            }, timeout=30)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                results.append({
                    "title": item["snippet"]["title"],
                    "channel": item["snippet"]["channelTitle"],
                    "published_at": item["snippet"]["publishedAt"],
                    "views": int(item["statistics"].get("viewCount", 0)),
                    "likes": int(item["statistics"].get("likeCount", 0)),
                    "duration": item["contentDetails"]["duration"],
                    "source": source_label,
                })
        except requests.RequestException as e:
            print(f"  UYARI: istatistik çekme başarısız ({type(e).__name__}), bu grup atlanıyor")
    return results


def fetch_monthly_high_performers(topic_hint: str, api_key: str, client) -> list:
    """Son 30 günde yayınlanmış ve MIN_VIEWS_THIS_MONTH üzerinde
    izlenme almış videoları, konuya özel arama terimleriyle bulur.
    Hiç sonuç yoksa boş liste döner (çağıran taraf yedeğe düşer)."""
    keywords = generate_search_keywords(topic_hint, client)
    if not keywords:
        return []

    print(f"  Üretilen arama terimleri: {keywords}")
    published_after = last_30_days_iso()

    all_ids = set()
    for kw in keywords:
        ids = search_video_ids(kw, api_key, published_after, RESULTS_PER_KEYWORD)
        all_ids.update(ids)

    if not all_ids:
        return []

    all_stats = fetch_stats(list(all_ids), api_key, source_label="monthly_high_performer")
    high_performers = [v for v in all_stats if v["views"] >= MIN_VIEWS_THIS_MONTH]
    return sorted(high_performers, key=lambda x: x["views"], reverse=True)


def fetch_general_niche_fallback(query: str, api_key: str) -> list:
    """Eski/genel yöntem: son 60 günün en çok izlenenleri, izlenme eşiği
    olmadan. Yüksek-performans araması boş dönerse devreye girer."""
    published_after = (
        datetime.datetime.utcnow() - datetime.timedelta(days=60)
    ).isoformat("T") + "Z"
    ids = search_video_ids(query, api_key, published_after, 15)
    return fetch_stats(ids, api_key, source_label="niche_fallback")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=False, default="",
                         help="Elle arama terimi verilirse yüksek-performans mantığı atlanır, direkt genel niş araması yapılır")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    api_key = os.environ["YOUTUBE_API_KEY"]
    topic_hint = os.environ.get("TOPIC_HINT", "").strip()

    if args.query.strip():
        print(f"Elle sorgu verildi: \"{args.query.strip()}\" - genel niş araması yapılıyor")
        trend_data = fetch_general_niche_fallback(args.query.strip(), api_key)
    else:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        print(f"Konu: \"{topic_hint or '(belirtilmemiş)'}\" - son 30 günün yüksek-performanslı "
              f"videoları (250k+ izlenme) aranıyor...")
        trend_data = fetch_monthly_high_performers(topic_hint, api_key, client)

        if not trend_data:
            print("  Yüksek-performans araması sonuçsuz, genel niş aramasına düşülüyor...")
            fallback_query = topic_hint.split(" - ")[0].strip() if topic_hint else random.choice(QUERY_POOL)
            trend_data = fetch_general_niche_fallback(fallback_query, api_key)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trend_data, f, ensure_ascii=False, indent=2)

    print(f"\nTrend analizi tamamlandı -> {args.out} ({len(trend_data)} video)")
    for v in trend_data[:8]:
        tag = "[SON 30 GÜN PATLADI]" if v["source"] == "monthly_high_performer" else "[GENEL]"
        print(f"  {tag} {v['views']:>10,} izlenme - {v['title']}")


if __name__ == "__main__":
    main()
