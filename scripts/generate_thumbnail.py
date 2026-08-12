"""
Seçilen tek başlık için TEK bir kapak (thumbnail) üretir ve üzerine
metin + kırmızı vurgu halkası bindirir (PIL).

MODEL MİMARİSİ (tamamen Gemini, tek sistem):
- Stil analizi (trend kapaklarından patern çıkarma): gemini-3.6-flash (vision)
- Kapak konsepti üretimi (visual_prompt/hook_text/emphasis_target): gemini-3.6-flash
- Vurgu noktası koordinat bulma (üretilen görselde): gemini-3.6-flash (vision)
- Arka plan görsel üretimi: gemini-2.5-flash-image ("Nano Banana" - ücretsiz
  katmanda erişilebilir model; gemini-3-pro-image/"Nano Banana Pro"
  denendi ama ücretsiz katman kotası SIFIR, faturalandırma gerektiriyor)
Sahne görselleri (generate_scenes.py) hâlâ Wiro kullanıyor - sadece
kapak üretimi Gemini'ye taşındı.

ÖNEMLİ (kapak-başlık ilişkisi):
Kapak, başlığın bir tekrarı DEĞİLDİR. Kapak, izleyiciye ham bir
SORU/GİZEM sunar (görsel + 2-3 kelimelik kışkırtıcı metin); başlık bu
sorunun bağlamını/gelişmesini verir ama cevabı vermez; videonun kendisi
asıl cevabı verir. Bu yüzden kapak için Gemini'den başlıktan bağımsız,
daha ham ve daha az bilgi veren bir "hook" konsepti üretiliyor.

TREND REFERANSI (--trends verilirse): trend_analysis.py'nin çektiği
nişte GERÇEKTEN tutan videoların BAŞLIKLARI (metin kalıpları) VE
KAPAK GÖRSELLERİ (gerçek, indirilip Gemini'ye vision ile gösterilen
JPEG'ler) bağlam olarak kullanılıyor. Amaç birebir kopyalamak değil,
"bu nişte şu enerji/ton/stil işe yarıyor" sinyalini kapak konseptine
yansıtmak - hem başlık hem görsel için, hâlâ tamamen orijinal bir
görsel/metin üretiliyor. Kapak görselleri sadece SOYUT PATERN (kontrast,
renk, kompozisyon) çıkarmak için kullanılır, hiçbir spesifik nesne/
logo/kişi kopyalanmaz (bkz. analyze_thumbnail_patterns).

KENDİ GEÇMİŞ KAPAKLARINDAN ÇEŞİTLİLİK KONTROLÜ (bugün eklendi):
trend_analysis.py'nin dış trend kapaklarını referans alması gibi,
generate_thumbnail.py artık KENDİ kanalının son 3 videosunun gerçek
kapaklarını da (YouTube OAuth ile) çekip Gemini'ye gösteriyor - ama
"bu stili taklit et" için değil, "bu spesifik motifleri (ör. çatlak
nesne + kırmızı parlayan çekirdek) TEKRARLAMA" diye. Bu, art arda
neredeyse birebir aynı görünen kapakların üretilmesini önlemek için
eklendi. Bkz. get_own_recent_thumbnails, analyze_own_thumbnail_diversity.

BUGÜNKÜ KRİTİK BUG DÜZELTMESİ (JSON parse): generate_thumbnail_concept
ve locate_emphasis_point, Gemini'nin yanıtını saf json.loads() ile
parse ediyordu - Gemini yanıtın başına/sonuna açıklama metni eklediğinde
bu SESSIZCE ÇÖKÜYORDU ve generate_thumbnail_concept HER SEFERİNDE aynı
sabit yedek değere ("a mysterious object under dramatic lighting,
close-up on one strange unexplained detail" + "NEVER EXPLAINED")
düşüyordu. Bu, art arda üretilen kapakların neden hep aynı klişeye
(çatlak nesne + kırmızı çekirdek) yakınsadığının ASIL SEBEBIYDI. Artık
extract_json_object() ile JSON gövdesi metin içinden güvenilir şekilde
çıkarılıyor, bu fallback artık neredeyse hiç tetiklenmeyecek.

NOT: YouTube'un native A/B testi (Test & Compare) API'den erişilemiyor
ve YPP üyeliği gerektiriyor, bu yüzden sistem artık çoklu kapak yerine
tek, en güçlü kapağı üretiyor (bkz. generate_titles.py).

GÖRSEL DİL (NESNE-ODAKLI FORMAT - avatar tamamen kaldırıldı):
Kanıtlanmış iki bağımsız sinyal (VidIQ kapak puanlaması + YouTube'un
kendi AI kapak önerisi) bu nişte avatar/yüz kullanmayan, tek bir
dramatik nesneye/detaya kilitlenen kapakların çok daha güçlü
performans gösterdiğini gösterdi. Bu yüzden:
- Kapağın TEK kahramanı, konuyla ilgili somut bir NESNE/DETAY (madeni
  para, eski telefon, oyun kartuşu vb.) - yakın çekim, dramatik ışık,
  net ve tek bakışta okunur.
- KIRMIZI VURGU ELEMENTİ (halka veya ok) nesnenin/detayın en kritik
  noktasını işaret eder - izleyicinin bakışını anında konuya kilitler.
- Metin sol altta, kenardan uzakta, koyu/yüksek kontrast bir kutu
  üzerinde, EN FAZLA 3 KELİME - CTR artırmak için daha punch'lı. Sadece
  beyaz/sarı (kırmızı BİLEREK metinde kullanılmıyor - vurgu halkasıyla
  çakışmasın diye, bkz. TEXT_COLOR_PALETTE).
- Arka plan hafif bulanık/derinlik hissi veren ikincil bağlam öğeleri
  içerebilir (ör. bulanık bir atari makinesi, oyun kutuları) ama asıl
  netlik/odak her zaman ön plandaki nesnede.
- Görsel içinde HİÇBİR yazı/tabela/el yazısı/okunabilir metin OLMAMALI
  (metni biz PIL ile kendimiz ekliyoruz, AI'nin sahne içine kendiliğinden
  tabela/yazı eklemesi hem kontrolsüz hem bizim eklediğimiz hook_text
  ile çakışabiliyor - bkz. BRAND_SAFETY_INSTRUCTION).
- Dört kenara kanal logosunun renginde ince bir çerçeve (marka
  tutarlılığı için).

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/ --script script.md --trends trend.json

Ortam değişkenleri:
    GEMINI_API_KEY (zorunlu)
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (opsiyonel - kendi
      geçmiş kapaklarından çeşitlilik kontrolü için; verilmezse bu adım
      sessizce atlanır, kapak üretimi normal devam eder)
"""
import argparse
import json
import os
import random
import textwrap
import time

import requests
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

MODEL_TEXT_VISION = "gemini-3.6-flash"  # konsept üretimi, stil analizi, koordinat bulma
MODEL_IMAGE = "gemini-2.5-flash-image"  # "Nano Banana" - kapak arka planı üretimi
# NOT: gemini-3-pro-image (Nano Banana Pro) denendi ama ücretsiz
# katmanda kotası SIFIR (limit: 0, faturalandırma/billing gerektiriyor).
# gemini-2.5-flash-image ücretsiz katmanda erişilebilir olduğu için
# buna düşüldü. İleride faturalandırma açılırsa Pro'ya terfi edilebilir.

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5  # saniye, üstel: 5, 10, 20, 40

TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
MAX_OWN_THUMBNAILS = 3  # çeşitlilik kontrolü için en fazla kaç geçmiş kapak

THUMBNAIL_STYLE = (
    "bright, vivid, eye-catching mystery-documentary YouTube thumbnail "
    "aesthetic, BOLD and CLEAR (not dark or moody), high saturation, "
    "punchy contrast, strong single subject that fills a significant "
    "portion of the frame and is INSTANTLY recognizable/readable at a "
    "glance even before reading any text, cinematic but vivid color "
    "grading (rich blues, warm oranges, bright reds - avoid muddy or "
    "underlit scenes), well-lit, sharp and pops even at small size, "
    "tech/gaming themed, no existing text in the image, 16:9"
)

BRAND_SAFETY_INSTRUCTION = (
    " Do NOT include any real trademarks, brand logos, copyrighted "
    "characters, or recognizable third-party product packaging - keep "
    "all objects generic/unbranded (e.g. an unmarked cartridge, a "
    "plain unlabeled box), not tied to any specific real company or IP. "
    "Do NOT include any signs, placards, labels, handwriting, or any "
    "readable text/words/numbers anywhere in the scene - the image "
    "must be completely free of text, since text is added separately."
)

FONT_PATH = "assets/fonts/Anton-Regular.ttf"

BORDER_COLOR = (205, 220, 57)
BORDER_WIDTH = 4

TEXT_COLOR_PALETTE = [
    (255, 255, 255),
    (255, 214, 0),
]

EMPHASIS_COLOR = (230, 30, 30)

MAX_TREND_TITLES = 8
MAX_TREND_THUMBNAILS = 3

FALLBACK_CONCEPTS = [
    {
        "visual_prompt": "a weathered object resting on a wooden desk under "
                          "warm desk-lamp light, long dramatic shadow, "
                          "shallow depth of field",
        "hook_text": "NEVER EXPLAINED",
        "emphasis_target": "the most worn/damaged part of the object",
    },
    {
        "visual_prompt": "a single object frozen mid-motion against a stark "
                          "dark background, cool blue rim lighting, high "
                          "contrast silhouette",
        "hook_text": "THE REAL REASON",
        "emphasis_target": "the sharpest edge or corner of the object",
    },
    {
        "visual_prompt": "an object photographed from a low dramatic angle, "
                          "dusty golden-hour light streaming across it, "
                          "gritty documentary texture",
        "hook_text": "HIDDEN COST",
        "emphasis_target": "the center of the object where light and shadow meet",
    },
]


def extract_json_object(raw: str) -> dict:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise json.JSONDecodeError("JSON objesi bulunamadı", cleaned, 0)
    return json.loads(cleaned[start:end + 1])


def call_gemini(client, contents, max_tokens=600):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL_TEXT_VISION,
                contents=contents,
                config=types.GenerateContentConfig(max_output_tokens=max_tokens),
            )
            return response.text or ""
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Gemini API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def load_trend_thumbnail_urls(trends_path: str) -> list:
    if not trends_path or not os.path.exists(trends_path):
        return []
    try:
        with open(trends_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)
        if not isinstance(trend_data, list):
            return []
        sorted_data = sorted(trend_data, key=lambda v: v.get("views", 0), reverse=True)
        urls = [v["thumbnail_url"] for v in sorted_data if v.get("thumbnail_url")]
        return urls[:MAX_TREND_THUMBNAILS]
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return []


def analyze_thumbnail_patterns(client, thumbnail_urls: list):
    if not thumbnail_urls:
        return None

    image_parts = []
    for url in thumbnail_urls:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            image_parts.append(types.Part.from_bytes(data=resp.content, mime_type="image/jpeg"))
        except Exception:
            continue

    if not image_parts:
        return None

    prompt_text = (
        "Bu görseller, bu nişte GERÇEKTEN yüksek izlenme almış YouTube "
        "kapaklarıdır. SADECE soyut, genel görsel PATERNLERİ çıkar: "
        "kompozisyon (konu nerede duruyor, kaç öğe var), kontrast "
        "seviyesi, renk paleti eğilimi, ışıklandırma tarzı, metin "
        "kullanımı var mı/nasıl. KESİNLİKLE şunları YAPMA: hiçbir "
        "spesifik nesneyi, markayı, logoyu, kişiyi ya da sahneyi tarif "
        "etme/kopyalama - amaç bu görselleri ya da içeriklerini "
        "yeniden üretmek DEĞİL, sadece hangi GENEL stilin bu nişte işe "
        "yaradığını anlamak.\n\n"
        "Çıktı SADECE 2-3 cümlelik bir stil özeti, düz metin (JSON değil)."
    )
    try:
        raw = call_gemini(client, image_parts + [prompt_text], max_tokens=250)
        return raw.strip() or None
    except Exception as e:
        print(f"  UYARI: kapak stil analizi başarısız ({type(e).__name__}), atlanıyor")
        return None


def get_youtube_access_token():
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        return None
    try:
        resp = requests.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["access_token"]
    except requests.RequestException as e:
        print(f"  UYARI: YouTube erişim token'ı alınamadı ({type(e).__name__}), "
              f"kendi kapak çeşitliliği kontrolü atlanıyor")
        return None


def get_own_recent_thumbnails(access_token: str, max_results: int = MAX_OWN_THUMBNAILS) -> list:
    if not access_token:
        return []
    try:
        resp = requests.get(YOUTUBE_SEARCH_URL, params={
            "part": "snippet",
            "forMine": "true",
            "type": "video",
            "order": "date",
            "maxResults": max_results,
        }, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        urls = []
        for item in items:
            thumbs = item.get("snippet", {}).get("thumbnails", {})
            for quality in ("high", "medium", "default"):
                url = thumbs.get(quality, {}).get("url", "")
                if url:
                    urls.append(url)
                    break
        return urls
    except requests.RequestException as e:
        print(f"  UYARI: kendi geçmiş kapakları çekilemedi ({type(e).__name__}), "
              f"çeşitlilik kontrolü atlanıyor")
        return []


def analyze_own_thumbnail_diversity(client, own_thumbnail_urls: list):
    if not own_thumbnail_urls:
        return None

    image_parts = []
    for url in own_thumbnail_urls:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            image_parts.append(types.Part.from_bytes(data=resp.content, mime_type="image/jpeg"))
        except Exception:
            continue

    if not image_parts:
        return None

    prompt_text = (
        "Bu görseller, AYNI YouTube kanalının EN SON yüklediği videoların "
        "kapaklarıdır (kronolojik sırada, en yeni dahil). Görevin: bu "
        "kapaklar arasında TEKRAR EDEN spesifik görsel motifleri tespit "
        "etmek (ör. 'çatlak/kırık bir nesne', 'nesnenin içinden parlayan "
        "kırmızı/turuncu ışık', 'aynı kamera açısı', 'aynı renk paleti'). "
        "Amaç, BİR SONRAKİ kapağın bu motifleri TEKRARLAMAMASI için bir "
        "'kaçınılacaklar' listesi çıkarmak - kanalın genel kalite/ton "
        "tutarlılığından BAHSETME, sadece spesifik, somut, tekrar eden "
        "görsel öğeleri listele. Eğer belirgin bir tekrar yoksa, boş "
        "liste döndür.\n\n"
        "Çıktı SADECE JSON dizi, kısa maddeler halinde: "
        '["motif 1", "motif 2"] ya da tekrar yoksa []'
    )
    try:
        raw = call_gemini(client, image_parts + [prompt_text], max_tokens=200)
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            return None
        motifs = json.loads(cleaned[start:end + 1])
        if isinstance(motifs, list) and motifs:
            return [str(m).strip() for m in motifs if str(m).strip()]
        return None
    except Exception as e:
        print(f"  UYARI: kendi kapak çeşitlilik analizi başarısız ({type(e).__name__}), atlanıyor")
        return None


def load_trend_titles(trends_path: str) -> list:
    if not trends_path or not os.path.exists(trends_path):
        return []
    try:
        with open(trends_path, "r", encoding="utf-8") as f:
            trend_data = json.load(f)
        if not isinstance(trend_data, list):
            return []
        return [v.get("title", "") for v in trend_data[:MAX_TREND_TITLES] if v.get("title")]
    except (json.JSONDecodeError, OSError):
        return []


def generate_thumbnail_concept(client, title: str, script_excerpt: str,
                                trend_titles: list, style_summary: str = None,
                                avoid_motifs: list = None) -> dict:
    trend_block = ""
    if trend_titles:
        trend_block = (
            "\n\nBU NİŞTE ŞU AN GERÇEKTEN TUTAN VİDEO BAŞLIKLARI (referans "
            "için - bunları KOPYALAMA, birebir tekrar etme, sadece hangi "
            "TON/ENERJİ/KELİME TARZI işe yaradığını anla ve kendi özgün "
            "hook_text'ine o enerjiyi yansıt):\n"
            + "\n".join(f"- {t}" for t in trend_titles)
        )

    style_block = ""
    if style_summary:
        style_block = (
            "\n\nBU NİŞTE GERÇEKTEN YÜKSEK PERFORMANS GÖSTERMİŞ KAPAKLARIN "
            "GENEL STİL ÖZETİ (gerçek görsellerden çıkarılmış, sadece "
            "PATERN - hiçbir spesifik görseli kopyalama, sadece bu genel "
            "stil eğilimini kendi özgün visual_prompt'una yansıt):\n"
            + style_summary
        )

    avoid_block = ""
    if avoid_motifs:
        avoid_block = (
            "\n\nÇEŞİTLİLİK ZORUNLULUĞU - BU MOTİFLERİ KESİNLİKLE TEKRARLAMA: "
            "Bu kanalın SON kapaklarında şu spesifik görsel öğeler tekrar "
            "tekrar kullanılmış, bu yüzden visual_prompt'un bunlardan "
            "GERÇEKTEN FARKLI olmalı (farklı nesne türü, farklı ışık "
            "kaynağı/rengi, farklı kompozyon/açı):\n"
            + "\n".join(f"- KAÇIN: {m}" for m in avoid_motifs)
        )

    prompt = f"""Bir YouTube kapak görseli (thumbnail) konsepti üret.

KESİN KURAL: Bu kapak, aşağıdaki başlıkla AYNI bilgiyi VERMEMELİ.
Başlık zaten konunun gelişmesini/bağlamını açıklıyor. Kapağın görevi
SADECE ham bir soru/gizem/çelişki sunmak - izleyici "bu ne, ne oluyor"
desin, başlıktaki bilgiyi henüz bilmesin.

BAŞLIK (kapakta bunu tekrar etme, bundan bağımsız düşün): {title}

SCRIPT'TEN KISA ALINTI (konunun özünü anlamak için, kapak metnine
doğrudan kopyalama): {script_excerpt[:800]}
{trend_block}
{style_block}
{avoid_block}

Üret:
1. "visual_prompt": İngilizce, somut, TEK BİR NESNEYE/DETAYA odaklanan
   bir SAHNE tarifi (ör. eski bir telefon, bir oyun kartuşu, bir madeni
   para, garip bir mekanizma). Yakın çekim, dramatik ışık, net ve tek
   bakışta ne olduğu anlaşılır olsun. Arka planda hafif bulanık
   ikincil/bağlamsal öğeler olabilir (ör. bulanık bir atari makinesi,
   oyun kutuları) ama net odak HER ZAMAN ön plandaki tek nesnede olsun.
   Kişi/karakter/yüz KULLANMA - bu formatta kapak tamamen nesne
   odaklı, insan figürü YOK. Sahnede HİÇBİR yazı/tabela/etiket OLMASIN.
2. "hook_text": İngilizce, TÜM BÜYÜK HARF, EN FAZLA 3 KELİME (2 kelime
   daha da güçlü olur), soru işareti kullanmadan, ŞOK EDİCİ/İDDİALI bir
   ifade - "interesting" değil "impossible to ignore" hissi versin
   (ör. "NEVER EXPLAINED", "THE REAL REASON", "HIDDEN COST"). Kelimeler
   ne kadar az, punch o kadar güçlü. Başlıktaki kelimeleri birebir
   tekrarlama.
3. "emphasis_target": İngilizce, TEK CÜMLE, visual_prompt'taki nesnenin
   TAM OLARAK HANGİ NOKTASININ/BÖLGESİNİN kırmızı bir halka veya okla
   vurgulanacağını tarif et (ör. "the small scratch mark near the
   center of the coin", "the rotary dial of the phone"). Bu, kırmızı
   vurgu elementinin nereye çizileceğini belirleyecek.

Çıktı SADECE JSON: {{"visual_prompt": "...", "hook_text": "...", "emphasis_target": "..."}}"""

    raw = call_gemini(client, prompt)
    try:
        return extract_json_object(raw)
    except json.JSONDecodeError:
        print(f"  UYARI: kapak konsepti JSON parse edilemedi, çeşitli "
              f"yedeklerden biri kullanılıyor. Ham yanıt: {raw[:200]!r}")
        return random.choice(FALLBACK_CONCEPTS)


def _extract_gemini_image_bytes(response):
    try:
        for part in response.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
    except (AttributeError, IndexError):
        pass
    return None


def generate_background(client, prompt: str, out_path: str):
    full_prompt = f"{THUMBNAIL_STYLE}. Subject: {prompt}.{BRAND_SAFETY_INSTRUCTION}"

    def _call(p: str):
        response = client.models.generate_content(
            model=MODEL_IMAGE,
            contents=p,
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                image_config=types.ImageConfig(aspect_ratio="16:9"),
            ),
        )
        return _extract_gemini_image_bytes(response)

    try:
        image_bytes = _call(full_prompt)
        if not image_bytes:
            raise RuntimeError("Gemini yanıtında görsel verisi yok")
        with open(out_path, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        print(f"  UYARI: Gemini kapak üretimi başarısız ({type(e).__name__}), "
              f"jenerik tarifle yeniden deniyorum...")
        fallback_prompt = (
            f"{THUMBNAIL_STYLE}. Subject: an abstract, dramatic "
            "technology-themed scene, glowing shapes, no people, no text."
            f"{BRAND_SAFETY_INSTRUCTION}"
        )
        image_bytes = _call(fallback_prompt)
        if not image_bytes:
            raise RuntimeError("Gemini yedek denemede de görsel üretemedi")
        with open(out_path, "wb") as f:
            f.write(image_bytes)


def draw_border(draw, img_w, img_h):
    for i in range(BORDER_WIDTH):
        draw.rectangle(
            [i, i, img_w - 1 - i, img_h - 1 - i],
            outline=BORDER_COLOR,
        )


def locate_emphasis_point(client, image_path: str, emphasis_target: str,
                           img_w: int, img_h: int):
    for attempt in range(2):
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

            prompt = (
                f"Bu görselde şunu bul: \"{emphasis_target}\". "
                f"Görsel {img_w}x{img_h} piksel boyutunda. Bu noktanın "
                f"MERKEZ piksel koordinatını (x, y) ve etrafına çizilecek "
                f"vurgu halkasının yarıçapını (radius, piksel) tahmin et. "
                f"Eğer bu detayı görselde NET olarak bulamıyorsan, "
                f"\"found\": false döndür.\n\n"
                f"Çıktı SADECE JSON: {{\"found\": true, \"x\": <int>, "
                f"\"y\": <int>, \"radius\": <int>}} ya da {{\"found\": false}}"
            )
            raw = call_gemini(client, [image_part, prompt], max_tokens=150)
            result = extract_json_object(raw)

            if not result.get("found", False):
                print(f"  UYARI: Gemini vurgu noktasını görselde bulamadı "
                      f"(deneme {attempt + 1}/2)")
                continue

            x, y, radius = int(result["x"]), int(result["y"]), int(result["radius"])
            x = max(0, min(img_w, x))
            y = max(0, min(img_h, y))
            radius = max(int(img_h * 0.08), min(int(img_h * 0.4), radius))
            return (x, y, radius)
        except Exception as e:
            print(f"  UYARI: vurgu noktası tespiti başarısız "
                  f"({type(e).__name__}, deneme {attempt + 1}/2)")

    print("  UYARI: 2 denemede de vurgu noktası bulunamadı, "
          "bu kapakta halka OLMADAN devam ediliyor (yanlış yere "
          "halka çizmek yerine)")
    return None


def draw_emphasis_ring(img: Image.Image, center_x: int, center_y: int, radius: int):
    draw = ImageDraw.Draw(img, "RGBA")
    ring_width = max(4, int(radius * 0.06))
    for i in range(ring_width):
        r = radius - i
        draw.ellipse(
            [center_x - r, center_y - r, center_x + r, center_y + r],
            outline=(*EMPHASIS_COLOR, 255),
        )
    return img


def overlay_text(image_path: str, hook_text: str, client, emphasis_target: str, out_path: str):
    img = Image.open(image_path).convert("RGB")

    emphasis_point = locate_emphasis_point(client, image_path, emphasis_target,
                                            img.width, img.height)
    if emphasis_point:
        ex, ey, eradius = emphasis_point
        img = draw_emphasis_ring(img, ex, ey, eradius)

    draw = ImageDraw.Draw(img, "RGBA")

    x_margin = int(img.width * 0.04)
    max_text_width = int(img.width * 0.6)
    max_text_height = int(img.height * 0.38)
    bottom_margin = int(img.height * 0.14)

    short_text = hook_text.strip().upper()

    font_size = int(img.height * 0.16)
    min_font_size = int(img.height * 0.06)

    while font_size > min_font_size:
        font = ImageFont.truetype(FONT_PATH, font_size)
        avg_char_w = font.getbbox("A")[2] - font.getbbox("A")[0]
        wrap_width = max(4, max_text_width // max(avg_char_w, 1))
        wrapped = textwrap.fill(short_text, width=wrap_width,
                                 break_long_words=False, break_on_hyphens=False)

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= max_text_width and text_h <= max_text_height:
            break
        font_size -= 4
    else:
        font = ImageFont.truetype(FONT_PATH, min_font_size)
        wrapped = textwrap.fill(short_text, width=10,
                                 break_long_words=False, break_on_hyphens=False)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_h = bbox[3] - bbox[1]

    x = x_margin
    y = img.height - bottom_margin - text_h
    pad = 16
    draw.rectangle(
        [x - pad, y - pad, x + (bbox[2] - bbox[0]) + pad, y + text_h + pad],
        fill=(0, 0, 0, 210),
    )

    for dx in (-3, -1, 0, 1, 3):
        for dy in (-3, -1, 0, 1, 3):
            draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                                 fill=(0, 0, 0, 255), spacing=10)
    text_color = random.choice(TEXT_COLOR_PALETTE)
    draw.multiline_text((x, y), wrapped, font=font, fill=(*text_color, 255), spacing=10)

    draw_border(draw, img.width, img.height)

    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", required=True, help="generate_titles.py çıktısı")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--script", required=False, default="script.md",
                         help="Kapak konsepti için bağlam olarak kullanılacak script dosyası")
    parser.add_argument("--trends", required=False, default="trend.json",
                         help="trend_analysis.py çıktısı - nişte tutan başlıkları/kapakları referans almak için")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)

    title = titles_data["selected"][0]

    script_excerpt = ""
    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script_excerpt = f.read()

    trend_titles = load_trend_titles(args.trends)
    if trend_titles:
        print(f"  {len(trend_titles)} trend başlığı referans olarak kullanılıyor")

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    trend_thumbnail_urls = load_trend_thumbnail_urls(args.trends)
    style_summary = None
    if trend_thumbnail_urls:
        style_summary = analyze_thumbnail_patterns(client, trend_thumbnail_urls)
        if style_summary:
            print(f"  {len(trend_thumbnail_urls)} gerçek kapaktan stil paterni çıkarıldı: {style_summary[:100]}...")
        else:
            print("  Kapak stil analizi başarısız/boş, referanssız devam ediliyor")

    avoid_motifs = None
    yt_access_token = get_youtube_access_token()
    if yt_access_token:
        own_thumbnail_urls = get_own_recent_thumbnails(yt_access_token)
        if own_thumbnail_urls:
            avoid_motifs = analyze_own_thumbnail_diversity(client, own_thumbnail_urls)
            if avoid_motifs:
                print(f"  Son {len(own_thumbnail_urls)} kapaktan {len(avoid_motifs)} "
                      f"tekrar eden motif tespit edildi, kaçınılacak: {avoid_motifs}")
            else:
                print("  Kendi kapaklarında belirgin bir tekrar tespit edilmedi")
    else:
        print("  YT OAuth bilgisi yok, kendi kapak çeşitliliği kontrolü atlanıyor")

    concept = generate_thumbnail_concept(client, title, script_excerpt, trend_titles,
                                          style_summary, avoid_motifs)

    raw_path = os.path.join(args.out_dir, "raw_1.png")
    generate_background(client, concept["visual_prompt"], raw_path)

    final_path = os.path.join(args.out_dir, "thumbnail_1.png")
    overlay_text(raw_path, concept["hook_text"], client,
                 concept.get("emphasis_target", ""), final_path)
    print(f"Kapak hazır -> {final_path}  "
          f"(hook: \"{concept['hook_text']}\", vurgu: \"{concept.get('emphasis_target', '')}\", "
          f"başlık: \"{title}\")")


if __name__ == "__main__":
    main()
