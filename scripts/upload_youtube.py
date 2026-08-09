"""
Üretilen videoyu YouTube'a OAuth ile yükler.

Zamanlama mantığı:
- Pipeline saat 21:00 TR'den ÖNCE biterse -> video "private" olarak
  yüklenir, publishAt = bugün 21:00 TR olarak ayarlanır, YouTube o
  saatte otomatik herkese açık yapar.
- Pipeline saat 21:00 TR'yi GEÇTİKTEN sonra biterse (örn. 21:13'te
  hazır olduysa) -> zamanlama yapılmaz, video doğrudan "public"
  olarak hemen yayınlanır (geçmiş bir saate zamanlamak YouTube'da
  hataya yol açar, bu yüzden bu durumda direkt yayına alıyoruz).

AI-üretilen içerik olduğu için "containsSyntheticMedia" bayrağı
otomatik True gönderiliyor (YouTube'un 2024 sonrası zorunlu kıldığı
sentetik/değiştirilmiş içerik beyanı).

Açıklamaya, VİDEOYA ÖZEL 5 hashtag eklenir (Claude ile, başlığa göre
üretilir). Ayrıca, mevcutsa iki EK BLOK daha eklenir (hashtag'lerin
ÜSTÜNDE, gövde metninin ALTINDA):
  - Chapters (bölüm zaman damgaları): script.md'nin bölümlerini,
    sesin toplam süresine ORANTILI konumlandırarak zaman damgalarına
    çevirir, her bölüm için Claude ile kısa bir başlık üretir.
    YouTube'un chapter kuralları sağlanmıyorsa blok TAMAMEN ATLANIR.
  - Sources (kaynaklar): facts.json'daki her fact'in "source" alanı,
    tekilleştirilip listelenir.

SABİTLENMİŞ YORUM: Video yüklendikten sonra, script'e özel 5 aday
yorum üretilir (Claude ile), her biri kendi içinde 1-10 puanlanır
(özgünlük, tartışma açma gücü kriterlerine göre). En yüksek puanlı
aday >=MIN_COMMENT_SCORE şartını sağlıyorsa kanal sahibi olarak
atılır (kanal sahibinin yorumu YouTube tarafından otomatik üstte
gösterilir, ekstra pin işlemi gerekmez). used_comments.json ile
geçmiş yorumlar takip edilir, aynı kalıpta yorum tekrarlanmaz.
Üretim/atma başarısız olursa sessizce atlanır, upload süreci ASLA
bu yüzden kesilmez.

Etiketler (tags) kanalın anahtar kelime listesinden dolduruluyor
(YouTube'un 500 karakter toplam sınırına uyacak şekilde).

Kullanım:
    python scripts/upload_youtube.py --video output/final.mp4 \
        --titles titles.json --thumbnail output/thumbnails/thumbnail_1.png
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone

import anthropic
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"

DEFAULT_CATEGORY_ID = "28"
TARGET_PUBLISH_HOUR_TR = 21  # Hedef yayın saati (Türkiye saati) - global (ABD/İngiltere) İngilizce kitle için optimize edildi

FALLBACK_HASHTAGS = ["#GamingScience", "#TechMysteries", "#GamingFacts", "#Gaming", "#TechHistory"]

MIN_CHAPTERS = 3
MIN_CHAPTER_GAP_SECONDS = 10

USED_COMMENTS_FILE = "used_comments.json"
MAX_COMMENTS_IN_PROMPT = 30
MIN_COMMENT_SCORE = 7

DESCRIPTION_BODY = (
    "{title}\n\n"
    "The Unseen Byte digs into the science, psychology, and hidden "
    "history that shape the games you play and the technology you use "
    "every day.\n\n"
    "This video was produced with AI-assisted narration and visuals.\n\n"
    "New videos weekly."
)

TAGS = [
    "gaming science", "tech mysteries", "gaming psychology",
    "hidden history technology", "gaming facts explained",
    "science of video games", "tech documentary", "video game history",
    "retro gaming facts", "esports science", "why games are addictive",
    "video game industry secrets", "tech history documentary",
    "hardware myths debunked", "gaming brain science",
    "unsolved tech mysteries", "gaming culture explained",
    "tech facts you didn't know", "forgotten technology",
    "gaming neuroscience",
]


def generate_hashtags(client, title: str) -> list:
    """Başlığa göre videoya ÖZEL 5 hashtag üretir (sabit liste değil).
    Herhangi bir hata/parse sorununda FALLBACK_HASHTAGS'e düşer, upload
    akışı asla bu yüzden kesilmez."""
    prompt = f"""Bu YouTube video başlığına göre, açıklamaya eklenecek
5 tane spesifik, videoya özel İngilizce hashtag üret (genel/sabit
hashtag değil, bu videonun konusuna gerçekten uygun olsun).

Başlık: "{title}"

SADECE JSON dizi formatında yaz, başka hiçbir şey yazma:
["#Etiket1", "#Etiket2", "#Etiket3", "#Etiket4", "#Etiket5"]"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        tags = json.loads(cleaned)
        if isinstance(tags, list) and len(tags) >= 3:
            return tags[:5]
    except Exception as e:
        print(f"  UYARI: hashtag üretimi başarısız ({type(e).__name__}), "
              f"sabit yedek hashtag'ler kullanılıyor")
    return FALLBACK_HASHTAGS


def load_facts_sources(facts_path: str) -> list:
    """facts.json'daki her fact'in 'source' alanını, sırayı koruyarak
    ve tekrarları eleyerek çıkarır. Dosya yoksa/bozuksa boş liste
    döner - pipeline asla kırılmaz."""
    if not os.path.exists(facts_path):
        return []
    try:
        with open(facts_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    facts = data.get("facts", []) if isinstance(data, dict) else []
    if not isinstance(facts, list):
        return []

    seen = set()
    sources = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        source = fact.get("source", "").strip()
        if source and source not in seen:
            seen.add(source)
            sources.append(source)
    return sources


def build_sources_block(sources: list) -> str:
    if not sources:
        return ""
    lines = "\n".join(f"- {s}" for s in sources)
    return f"Sources referenced in this video:\n{lines}"


def split_script_sections(script_text: str) -> list:
    """script.md'yi bölüm bazlı böler (script_prompt.md'nin ürettiği
    hali: bölümler SADECE boş satırla ayrılmış)."""
    return [s.strip() for s in re.split(r"\n{2,}", script_text) if s.strip()]


def generate_chapter_titles(client, sections: list):
    """Her bölüm için kısa (3-5 kelime) bir başlık üretir. Başarısız
    olursa ya da bölüm sayısıyla eşleşmezse None döner."""
    joined = "\n---\n".join(f"[{i}] {s[:400]}" for i, s in enumerate(sections))
    prompt = f"""Aşağıda bir YouTube videosunun bölümleri (paragraflar)
var. Her biri için, o bölümde ANLATILAN konuyu özetleyen KISA (3-5
kelime) bir başlık üret - YouTube video bölümleri (chapters) için
kullanılacak. İngilizce, başlık formatında (Title Case).

BÖLÜMLER:
{joined}

Çıktı SADECE JSON dizi, sırayla, tam olarak {len(sections)} eleman:
["Title 1", "Title 2", ...]"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        titles = json.loads(cleaned)
        if isinstance(titles, list) and len(titles) == len(sections):
            return titles
    except Exception as e:
        print(f"  UYARI: bölüm başlıkları üretilemedi ({type(e).__name__}), "
              f"chapters bloğu atlanacak")
    return None


def load_alignment_words(alignment_path: str) -> list:
    if not os.path.exists(alignment_path):
        return []
    try:
        with open(alignment_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def format_chapter_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def compute_chapter_timestamps(sections: list, alignment_words: list):
    """Her bölümün başladığı zamanı, script'teki kelime konumunu sesin
    toplam süresine ORANTILI olarak eşleyerek tahmin eder."""
    if not alignment_words or not sections:
        return []

    total_words = sum(len(s.split()) for s in sections)
    if total_words == 0:
        return []

    total_duration = alignment_words[-1].get("start", 0)
    if not total_duration or total_duration <= 0:
        return []

    timestamps = []
    cumulative = 0
    for section in sections:
        fraction = cumulative / total_words
        timestamps.append(fraction * total_duration)
        cumulative += len(section.split())

    timestamps[0] = 0.0
    return timestamps


def build_chapters_block(sections: list, titles, timestamps: list) -> str:
    """YouTube'un chapters kurallarını sağlamıyorsa boş string döner."""
    if not titles or len(titles) != len(sections) or len(timestamps) != len(sections):
        return ""
    if len(sections) < MIN_CHAPTERS:
        return ""
    if timestamps[0] != 0.0:
        return ""

    for i in range(1, len(timestamps)):
        if timestamps[i] - timestamps[i - 1] < MIN_CHAPTER_GAP_SECONDS:
            return ""

    lines = [
        f"{format_chapter_timestamp(t)} {title}"
        for t, title in zip(timestamps, titles)
    ]
    return "\n".join(lines)


def load_used_comments() -> list:
    if not os.path.exists(USED_COMMENTS_FILE):
        return []
    try:
        with open(USED_COMMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_used_comments(comments: list):
    with open(USED_COMMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)


def generate_pinned_comment(client, script_text: str, used_comments: list):
    """Script'e özel, tartışma açan, özgün 5 aday yorum üretir, her
    birini kendi içinde puanlar, en yüksek puanlıyı (>=MIN_COMMENT_SCORE
    şartıyla) döner. Uygun aday yoksa ya da hata olursa None döner -
    çağıran taraf yorum atmadan devam eder, pipeline kırılmaz."""
    avoid_block = ""
    if used_comments:
        recent = used_comments[-MAX_COMMENTS_IN_PROMPT:]
        avoid_block = (
            "\n\nDAHA ÖNCE KULLANILAN YORUMLAR (bunlara BENZER, aynı kalıpta "
            "bir yorum ÜRETME, tamamen farklı bir açı/soru bul):\n"
            + "\n".join(f"- {c}" for c in recent)
        )

    prompt = f"""Bu YouTube videosunun script'ine göre, videonun ALTINA
sabitlenecek (pinned comment) bir yorum üreteceksin. Amaç izleyicileri
gerçekten düşünmeye ve YANIT VERMEYE zorlamak - klasik "ne düşünüyorsun?"
gibi basit/klişe sorular DEĞİL, script'teki spesifik bir detaya dayanan,
tartışma açan, biraz iddialı ya da beklenmedik bir açı sunan sorular.

SCRIPT (ilk 2000 karakter):
{script_text[:2000]}
{avoid_block}

5 farklı aday yorum üret, her biri script'in FARKLI bir anına/detayına
odaklansın, birbirine benzemesin. Her biri için kendi kendine 1-10
arası puan ver (kriter: özgünlük, script'e özgülük, ne kadar gerçek
tartışma/yanıt tetikleyeceği - klişe/genel sorular düşük puan alsın).

Çıktı SADECE JSON dizi, 5 eleman:
[{{"comment": "...", "score": 8}}, ...]"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        candidates = json.loads(cleaned)
        if not isinstance(candidates, list) or not candidates:
            return None

        best = max(candidates, key=lambda c: c.get("score", 0))
        if best.get("score", 0) >= MIN_COMMENT_SCORE:
            return best.get("comment", "").strip()
    except Exception as e:
        print(f"  UYARI: yorum üretimi başarısız ({type(e).__name__})")
    return None


def post_and_pin_comment(access_token: str, video_id: str, comment_text: str):
    """Yorumu kanal sahibi olarak atar (kanal sahibinin yorumu YouTube
    tarafından otomatik üstte gösterilir, ekstra pin işlemi gerekmez).
    Başarısız olursa sessizce loglar, upload süreci ASLA bu yüzden
    kesilmez."""
    try:
        resp = requests.post(
            f"{COMMENTS_URL}?part=snippet",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": comment_text}
                    },
                }
            },
            timeout=30,
        )
        resp.raise_for_status()
        print(f"Yorum atıldı ve sabitlendi: \"{comment_text}\"")
        return True
    except requests.RequestException as e:
        print(f"  UYARI: yorum atılamadı ({type(e).__name__}), video yine de yayında kalır")
        return False


def build_tags_within_limit(tags: list, limit: int = 480) -> list:
    result, total = [], 0
    for tag in tags:
        total += len(tag) + 1
        if total > limit:
            break
        result.append(tag)
    return result


def compute_publish_at() -> str | None:
    """
    Şu an TR saatiyle hedef saati geçmediyse, bugün TARGET_PUBLISH_HOUR_TR
    saatinin UTC/RFC3339 karşılığını döndürür (zamanlı yayın için).
    Hedef saat zaten geçtiyse None döner (bu, "hemen yayınla" demektir).
    """
    tr_tz = timezone(timedelta(hours=3))  # Europe/Istanbul (DST'siz sabit ofset)
    now_tr = datetime.now(tr_tz)
    target_tr = now_tr.replace(
        hour=TARGET_PUBLISH_HOUR_TR, minute=0, second=0, microsecond=0
    )

    if now_tr >= target_tr:
        return None

    return target_tr.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_access_token() -> str:
    resp = requests.post(TOKEN_URL, data={
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def initiate_upload(access_token: str, title: str, description: str, video_size: int) -> tuple:
    publish_at = compute_publish_at()

    status = {
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    else:
        status["privacyStatus"] = "public"

    metadata = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": build_tags_within_limit(TAGS),
            "categoryId": DEFAULT_CATEGORY_ID,
        },
        "status": status,
    }
    resp = requests.post(
        f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(video_size),
            "X-Upload-Content-Type": "video/mp4",
        },
        json=metadata,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.headers["Location"], publish_at


def upload_video_bytes(upload_url: str, video_path: str) -> dict:
    with open(video_path, "rb") as f:
        video_data = f.read()
    resp = requests.put(
        upload_url,
        headers={"Content-Type": "video/mp4"},
        data=video_data,
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


def set_thumbnail(access_token: str, video_id: str, thumbnail_path: str):
    with open(thumbnail_path, "rb") as f:
        image_data = f.read()
    resp = requests.post(
        f"{THUMBNAIL_URL}?videoId={video_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/png",
        },
        data=image_data,
        timeout=60,
    )
    resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--titles", required=True)
    parser.add_argument("--thumbnail", required=False, default=None)
    parser.add_argument("--script", required=False, default="script.md",
                         help="Chapters (bölüm zaman damgaları) ve sabitlenmiş yorum için kaynak script")
    parser.add_argument("--facts", required=False, default="facts.json",
                         help="Sources (kaynaklar) bloğu için facts.json yolu")
    parser.add_argument("--alignment", required=False, default="audio/alignment.json",
                         help="Chapters için kelime zaman damgaları (Whisper çıktısı)")
    args = parser.parse_args()

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)
    title = titles_data["selected"][0]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    hashtags = generate_hashtags(client, title)
    print(f"Üretilen hashtag'ler: {' '.join(hashtags)}")

    extra_blocks = []
    script_text = ""

    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script_text = f.read()
        sections = split_script_sections(script_text)
        alignment_words = load_alignment_words(args.alignment)
        timestamps = compute_chapter_timestamps(sections, alignment_words)
        chapter_titles = generate_chapter_titles(client, sections) if timestamps else None
        chapters_block = build_chapters_block(sections, chapter_titles, timestamps) if chapter_titles else ""
        if chapters_block:
            extra_blocks.append(chapters_block)
            print(f"Bölüm zaman damgaları (chapters) eklendi ({len(sections)} bölüm).")
        else:
            print("  UYARI: chapters bloğu için yeterli/uyumlu veri yok, atlanıyor.")

    sources = load_facts_sources(args.facts)
    sources_block = build_sources_block(sources)
    if sources_block:
        extra_blocks.append(sources_block)
        print(f"{len(sources)} kaynak açıklamaya eklendi.")
    else:
        print("  UYARI: kaynak bulunamadı, sources bloğu atlanıyor.")

    extra_text = ("\n\n" + "\n\n".join(extra_blocks)) if extra_blocks else ""
    description = (
        DESCRIPTION_BODY.format(title=title)
        + extra_text
        + "\n\n" + " ".join(hashtags)
    )

    video_size = os.path.getsize(args.video)

    print("Access token alınıyor...")
    access_token = get_access_token()

    print(f"Yükleme başlatılıyor: \"{title}\"")
    upload_url, publish_at = initiate_upload(access_token, title, description, video_size)

    print("Video yükleniyor (bu birkaç dakika sürebilir)...")
    result = upload_video_bytes(upload_url, args.video)
    video_id = result["id"]

    if publish_at:
        print(f"Video yüklendi -> https://youtube.com/watch?v={video_id}")
        print(f"(private, {publish_at} UTC'de -bugün {TARGET_PUBLISH_HOUR_TR}:00 TR- otomatik yayına girecek)")
    else:
        print(f"Video yüklendi -> https://youtube.com/watch?v={video_id} (public, hemen yayınlandı)")

    if args.thumbnail and os.path.exists(args.thumbnail):
        print("Kapak ayarlanıyor...")
        set_thumbnail(access_token, video_id, args.thumbnail)
        print("Kapak ayarlandı.")

    if script_text:
        used_comments = load_used_comments()
        pinned_comment = generate_pinned_comment(client, script_text, used_comments)
        if pinned_comment:
            posted = post_and_pin_comment(access_token, video_id, pinned_comment)
            if posted:
                used_comments.append(pinned_comment)
                save_used_comments(used_comments)
        else:
            print("  UYARI: yeterli puanlı yorum üretilemedi, yorum atlanıyor")

    if publish_at:
        print(f"\nTAMAMLANDI. Video {TARGET_PUBLISH_HOUR_TR}:00 TR saatinde otomatik yayına girecek.")
    else:
        print(f"\nTAMAMLANDI. Saat {TARGET_PUBLISH_HOUR_TR}:00 TR zaten geçtiği için video hemen HERKESE AÇIK olarak yayınlandı.")
    print("İstersen YouTube Studio'dan A/B Testing (Test & Compare) ile")
    print("diğer 2 kapak/başlığı da ekleyebilirsin.")


if __name__ == "__main__":
    main()
