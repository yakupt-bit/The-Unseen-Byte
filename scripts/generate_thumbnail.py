"""
Seçilen tek başlık için TEK bir kapak (thumbnail) üretir ve üzerine
metin + (opsiyonel) kırmızı vurgu halkası bindirir (PIL).

MODEL MİMARİSİ (tamamen Gemini, tek sistem):
- Stil analizi (trend kapaklarından patern çıkarma): gemini-3.6-flash (vision)
- Kapak konsepti üretimi (visual_prompt/hook_text/emphasis_target): gemini-3.6-flash
- Vurgu noktası koordinat bulma (üretilen görselde): gemini-3.6-flash (vision)
- Metin yerleşim bölgesi bulma (üretilen görselde): gemini-3.6-flash (vision)
- Arka plan görsel üretimi: gemini-2.5-flash-image ("Nano Banana")

--- BUGÜNKÜ BÜYÜK REVİZYON (gerçek VidIQ verisine dayanarak) ---
Kullanıcı elle ürettiği bir kapağı (geniş, atmosferik, sinematik bir
atölye sahnesi + kutu olmadan büyük, gölgeli başlık metni) pipeline'ın
ürettiği eski formülle (yakın çekim nesne + kırmızı parlayan çekirdek +
siyah kutu içinde kısa "NEVER EXPLAINED" metni) karşılaştırdı. VidIQ
kapak puanları: manuel kapak 70/100, eski pipeline formülü 37/100 -
neredeyse İKİ KAT fark. Bu veriye dayanarak sistem şu şekilde revize
edildi:

1. GÖRSEL STİL: "bright/vivid/bright reds" zorunluluğu kaldırıldı.
   Artık GENİŞ, ATMOSFERİK, sinematik bir SAHNE tercih ediliyor (tozlu
   ışık huzmeleri, derinlik, bağlamsal öğeler) - eskiden zorunlu olan
   "tek nesneye yakın çekim" formülü gevşetildi, hâlâ net bir odak
   noktası olmalı ama sahne artık "boş/soyut" değil, hikaye anlatan
   bir ortam olabilir.
2. METİN KUTUSU KALDIRILDI: Eskiden metnin arkasında dolgun siyah bir
   dikdörtgen vardı (kaybeden kapakta böyleydi). Artık SADECE siyah
   kontur/gölge ile okunabilirlik sağlanıyor, kutu yok (kazanan
   kapaktaki gibi) - görsel çok daha temiz duruyor.
3. METİN UZUNLUĞU GEVŞETİLDİ: Eskiden KESİN 2-3 kelime şartı vardı.
   Artık 2-6 kelime arası, 1-3 satır, başlığın özünü yansıtan daha
   bilgilendirici bir metin serbest - kazanan kapak neredeyse başlığın
   kendisini kullanmıştı ve çok daha iyi puan aldı.
4. KIRMIZI VURGU HALKASI OPSİYONEL HALE GETİRİLDİ: Konsept üretimi artık
   "use_emphasis_ring" alanı ile halkanın bu sahnede GERÇEKTEN faydalı
   olup olmadığına karar veriyor - geniş atmosferik sahnelerde genelde
   gereksiz/dikkat dağıtıcı olduğu için varsayılan eğilim artık HAYIR.

DİNAMİK METİN YERLEŞİMİ: Üretilen görsel Gemini'ye (vision) gösterilip
görseldeki EN BOŞ/EN UYGUN köşe (üst-sol, üst-sağ, alt-sol, alt-sağ)
sorduruluyor - ana sahne/nesneyle çakışmayan bir bölge seçilir. Tespit
başarısız olursa dört köşeden rastgele biri seçilir.

GÜÇLENDİRİLMİŞ RAKİP/VİRAL STİL ANALİZİ: analyze_thumbnail_patterns,
yüz/avatar içermeyen rakip kapaklardan stil paterni + metin konumu
eğilimi çıkarır.

KENDİ GEÇMİŞ KAPAKLARINDAN ÇEŞİTLİLİK KONTROLÜ: KENDİ kanalının son 3
videosunun gerçek kapaklarını (YouTube OAuth ile) çekip Gemini'ye
gösteriyor - "bu spesifik motifleri TEKRARLAMA" diye.

JSON PARSE SAĞLAMLIĞI: extract_json_object() ile JSON gövdesi metin
içinden güvenilir şekilde çıkarılıyor - eskiden saf json.loads() Gemini
yanıtın başına/sonuna açıklama eklediğinde sessizce çöküyor ve HER
SEFERİNDE aynı sabit yedek değere düşülüyordu.

Kullanım:
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/
    python scripts/generate_thumbnail.py --titles titles.json --out-dir output/thumbnails/ --script script.md --trends trend.json

Ortam değişkenleri:
    GEMINI_API_KEY (zorunlu)
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (opsiyonel - kendi
      geçmiş kapaklarından çeşitlilik kontrolü için)
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

MODEL_TEXT_VISION = "gemini-3.6-flash"
MODEL_IMAGE = "gemini-2.5-flash-image"

MAX_RETRIES = 4
RETRY_BASE_DELAY = 5

TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
MAX_OWN_THUMBNAILS = 3

# ESKİ (kaybeden, VidIQ 37/100): "bright, vivid... bright reds..."
# YENİ (kazanan yaklaşımına göre revize, geniş atmosferik sahne):
THUMBNAIL_STYLE = (
    "cinematic, atmospheric, documentary-style YouTube thumbnail scene, "
    "wide environmental composition with real depth (not just a flat "
    "close-up) - think a dusty archive, a cluttered workshop desk, an "
    "old server room, or a similar richly-detailed physical space "
    "relevant to the topic. Moody but CLEAR directional lighting (a "
    "single strong light source such as a lamp, a beam of light "
    "through dust particles, or a glowing screen) that draws the eye "
    "to one clear focal point without the whole image looking dark or "
    "flat. Rich, slightly desaturated cinematic color grading (deep "
    "blues, warm ambers, muted teals - avoid oversaturated neon colors "
    "and avoid making everything glow bright red). Sharp focus, "
    "believable physical textures (wood grain, dust, worn metal, old "
    "electronics), tech/gaming/history themed, no existing text in the "
    "image, 16:9"
)

BRAND_SAFETY_INSTRUCTION = (
    " Do NOT include any real trademarks, brand logos, copyrighted "
    "characters, or recognizable third-party product packaging - keep "
    "all objects generic/unbranded (e.g. an unmarked cartridge, a "
    "plain unlabeled box), not tied to any specific real company or IP. "
    "If people appear in the scene, they must be GENERIC/FICTIONAL "
    "figures - NOT a real, identifiable public figure, celebrity, or "
    "executive, and not wearing any real company's branded clothing/"
    "badges. Do NOT include any signs, placards, labels, handwriting, "
    "or any readable text/words/numbers anywhere in the scene - the "
    "image must be completely free of text, since text is added "
    "separately."
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

TEXT_ZONES = ["top_left", "top_right", "bottom_left", "bottom_right"]

FALLBACK_CONCEPTS = [
    {
        "visual_prompt": "a cluttered wooden workshop desk at night, a "
                          "single warm desk lamp casting a dramatic beam "
                          "of light across scattered old electronics and "
                          "tools, dust particles visible in the light, "
                          "deep shadows in the background, cinematic depth",
        "hook_text": "THE TRUTH BEHIND IT",
        "emphasis_target": "",
        "use_emphasis_ring": False,
    },
    {
        "visual_prompt": "a dimly lit archive room with old shelves full "
                          "of technology and files, a single shaft of "
                          "cool blue light cutting through the dust, one "
                          "object left open on a table in the foreground, "
                          "moody and mysterious depth",
        "hook_text": "WHAT THEY DIDN'T SAY",
        "emphasis_target": "",
        "use_emphasis_ring": False,
    },
    {
        "visual_prompt": "a close but contextual shot of a worn object on "
                          "an old desk, warm golden-hour light streaming "
                          "in from one side, gritty documentary texture, "
                          "background softly out of focus with hints of "
                          "the wider room",
        "hook_text": "THE HIDDEN COST",
        "emphasis_target": "the most worn/damaged part of the object",
        "use_emphasis_ring": True,
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
        "kapaklarıdır (rakip kanallar dahil). Bu kapaklar arasında "
        "ÖZELLİKLE yüz/avatar İÇERMEYEN örnekleri baz alarak SADECE "
        "soyut, genel görsel PATERNLERİ çıkar: kompozisyon (geniş sahne "
        "mi yoksa yakın çekim mi, kaç öğe var, ne kadar atmosferik/"
        "sinematik), kontrast seviyesi, renk paleti eğilimi, "
        "ışıklandırma tarzı, VE metin varsa GENELDE görselin hangi "
        "bölgesinde durduğu ve NE KADAR UZUN/kısa olduğu, kutu içinde "
        "mi yoksa doğrudan görsel üzerine mi bindirildiği. KESİNLİKLE "
        "şunları YAPMA: hiçbir spesifik nesneyi, markayı, logoyu, "
        "kişiyi ya da sahneyi tarif etme/kopyalama - amaç bu görselleri "
        "ya da içeriklerini yeniden üretmek DEĞİL, sadece hangi GENEL "
        "stilin bu nişte (yüzsüz formatta) işe yaradığını anlamak.\n\n"
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
        "kırmızı/turuncu ışık', 'aynı kamera açısı', 'aynı renk paleti', "
        "'metin arkasında siyah kutu'). Amaç, BİR SONRAKİ kapağın bu "
        "motifleri TEKRARLAMAMASI için bir 'kaçınılacaklar' listesi "
        "çıkarmak. Eğer belirgin bir tekrar yoksa, boş liste döndür.\n\n"
        "Çıktı SADECE JSON dizi: [\"motif 1\", \"motif 2\"] ya da tekrar yoksa []"
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
            "\n\nBU NİŞTE (RAKİP KANALLAR DAHİL) GERÇEKTEN YÜKSEK "
            "PERFORMANS GÖSTERMİŞ KAPAKLARIN GENEL STİL ÖZETİ:\n"
            + style_summary
        )

    avoid_block = ""
    if avoid_motifs:
        avoid_block = (
            "\n\nÇEŞİTLİLİK ZORUNLULUĞU - BU MOTİFLERİ KESİNLİKLE TEKRARLAMA:\n"
            + "\n".join(f"- KAÇIN: {m}" for m in avoid_motifs)
        )

    prompt = f"""Bir YouTube kapak görseli (thumbnail) konsepti üret.

ÖNEMLİ BAĞLAM - GERÇEK PERFORMANS VERİSİ: Bu kanalda yakın zamanda
yapılan bir A/B karşılaştırmasında, GENİŞ/ATMOSFERİK bir SAHNE (tozlu
bir atölye masası, arşiv odası gibi, derinlik ve bağlam içeren) +
KUTUSUZ, başlığa yakın uzunlukta (birkaç kelime, 1-3 satır) metin
içeren bir kapak, VidIQ kalite puanlamasında YAKIN ÇEKİM tek nesne +
kırmızı parlayan ışık + kutu içinde 2 kelimelik jenerik metin içeren
eski formülden NEREDEYSE İKİ KAT daha yüksek puan aldı (70 vs 37).
Bu yüzden BUGÜNDEN İTİBAREN geniş/atmosferik sahne ve daha
bilgilendirici metin yaklaşımı TERCİH EDİLMELİ.

KESİN KURAL: Bu kapak, aşağıdaki başlıkla BİREBİR AYNI CÜMLE olmamalı
ama başlığın MERAK UYANDIRAN özünü/sorusunu yansıtabilir - tamamen
alakasız, aşırı soyut bir "hook" kelimesi ARAMA, doğrudan konuya
değinen ama tam cevabı vermeyen bir ifade tercih et.

BAŞLIK: {title}

SCRIPT'TEN KISA ALINTI: {script_excerpt[:800]}
{trend_block}
{style_block}
{avoid_block}

Üret:
1. "visual_prompt": İngilizce, GENİŞ VE ATMOSFERİK bir SAHNE tarifi -
   sadece tek bir nesneye aşırı yakın çekim DEĞİL, gerçek bir mekan/
   bağlam hissi olan bir sahne. Net bir ışık kaynağı ve net bir odak
   noktası olsun ama sahne "boş" durmasın, derinlik ve doku hissi
   versin. EĞER konu/script buna uygunsa (ör. iki taraf/şirket arasında
   bir çatışma, bir müzakere, bir yüzleşme, bir karar anı), İNSAN
   FİGÜRÜ/KARAKTER kullanarak script'in EN DRAMATİK anını canlandır -
   jenerik/kurgusal figürler olsun, gerçek bir kişi/ünlü OLMASIN. Konu
   saf teknik/nesne odaklıysa (insan draması yoksa) yine nesne-odaklı
   yakın çekim formatını tercih et - insan figürü ZORUNLU değil, sadece
   hikayeyi güçlendiriyorsa kullan. Sahnede HİÇBİR yazı/tabela/etiket
   OLMASIN.
2. "hook_text": İngilizce, TÜM BÜYÜK HARF, 2 İLA 6 KELİME ARASI (kesin
   2-3 kelime şartı YOK artık), 1-3 satıra bölünebilir, başlığın
   merakını yansıtan, iddialı ama tamamen soyut olmayan bir ifade.
   HER SEFERİNDE FARKLI VE ÖZGÜN - "NEVER EXPLAINED" gibi tek bir
   kalıba saplanma.
3. "use_emphasis_ring": true/false - bu SAHNE için kırmızı bir vurgu
   halkası GERÇEKTEN faydalı mı (net, tek bir küçük detayı işaret
   etmek gerekiyorsa true) yoksa geniş atmosferik sahnede gereksiz/
   dikkat dağıtıcı mı olur (false)? ŞÜPHEDEYSEN false seç - veri, sade
   sahnelerin daha iyi performans gösterdiğini gösteriyor.
4. "emphasis_target": SADECE use_emphasis_ring true ise doldur -
   İngilizce, TEK CÜMLE, hangi noktanın vurgulanacağını tarif et. false
   ise boş string bırak.

Çıktı SADECE JSON: {{"visual_prompt": "...", "hook_text": "...", "use_emphasis_ring": true veya false, "emphasis_target": "..."}}"""

    raw = call_gemini(client, prompt)
    try:
        concept = extract_json_object(raw)
        concept.setdefault("use_emphasis_ring", False)
        concept.setdefault("emphasis_target", "")
        return concept
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
            f"{THUMBNAIL_STYLE}. Subject: an abstract, atmospheric "
            "technology-themed scene, soft directional light, no people, no text."
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
          "bu kapakta halka OLMADAN devam ediliyor")
    return None


def locate_text_zone(client, image_path: str, img_w: int, img_h: int,
                      emphasis_point=None) -> str:
    emphasis_note = ""
    if emphasis_point:
        ex, ey, _ = emphasis_point
        emphasis_note = (
            f" Görselde ({ex}, {ey}) civarında kırmızı bir vurgu halkası "
            f"var, metin bölgesi bu noktayla ÇAKIŞMAMALI."
        )
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

        prompt = (
            f"Bu görsele bak. Görsel {img_w}x{img_h} piksel boyutunda. "
            f"Ana sahnenin/konunun EN AZ bulunduğu, en 'boş'/sade kalan "
            f"köşeyi bul - bir YouTube kapağına birkaç kelimelik bir "
            f"başlık metni eklenecek, bu metin ana görsel öğelerin "
            f"üzerine binmemeli."
            f"{emphasis_note}\n\n"
            f"Çıktı SADECE JSON: "
            f'{{"zone": "top_left" | "top_right" | "bottom_left" | "bottom_right"}}'
        )
        raw = call_gemini(client, [image_part, prompt], max_tokens=80)
        result = extract_json_object(raw)
        zone = result.get("zone", "")
        if zone in TEXT_ZONES:
            return zone
    except Exception as e:
        print(f"  UYARI: metin yerleşim bölgesi tespiti başarısız "
              f"({type(e).__name__})")

    fallback_zone = random.choice(TEXT_ZONES)
    print(f"  Metin bölgesi tespit edilemedi, rastgele seçildi: {fallback_zone}")
    return fallback_zone


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


def overlay_text(image_path: str, hook_text: str, client, emphasis_target: str,
                  use_emphasis_ring: bool, out_path: str):
    img = Image.open(image_path).convert("RGB")

    # Vurgu halkası artık OPSİYONEL - veri, sade/atmosferik sahnelerde
    # halkanın çoğunlukla gereksiz/dikkat dağıtıcı olduğunu gösterdi.
    emphasis_point = None
    if use_emphasis_ring and emphasis_target:
        emphasis_point = locate_emphasis_point(client, image_path, emphasis_target,
                                                img.width, img.height)
        if emphasis_point:
            ex, ey, eradius = emphasis_point
            img = draw_emphasis_ring(img, ex, ey, eradius)

    zone = locate_text_zone(client, image_path, img.width, img.height, emphasis_point)

    draw = ImageDraw.Draw(img, "RGBA")

    edge_margin = int(img.width * 0.045)
    top_margin = int(img.height * 0.06)
    bottom_margin = int(img.height * 0.08)
    # Kutu kaldırıldığı için metin artık daha geniş bir alanı
    # kaplayabilir (kazanan örnekteki gibi çok satırlı, büyük başlık).
    max_text_width = int(img.width * 0.62)
    max_text_height = int(img.height * 0.55)

    short_text = hook_text.strip().upper()

    font_size = int(img.height * 0.12)
    min_font_size = int(img.height * 0.05)

    while font_size > min_font_size:
        font = ImageFont.truetype(FONT_PATH, font_size)
        avg_char_w = font.getbbox("A")[2] - font.getbbox("A")[0]
        wrap_width = max(4, max_text_width // max(avg_char_w, 1))
        wrapped = textwrap.fill(short_text, width=wrap_width,
                                 break_long_words=False, break_on_hyphens=False)

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=12)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= max_text_width and text_h <= max_text_height:
            break
        font_size -= 4
    else:
        font = ImageFont.truetype(FONT_PATH, min_font_size)
        wrapped = textwrap.fill(short_text, width=10,
                                 break_long_words=False, break_on_hyphens=False)
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=12)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

    if zone == "top_left":
        x = edge_margin
        y = top_margin
    elif zone == "top_right":
        x = img.width - edge_margin - text_w
        y = top_margin
    elif zone == "bottom_right":
        x = img.width - edge_margin - text_w
        y = img.height - bottom_margin - text_h
    else:  # bottom_left
        x = edge_margin
        y = img.height - bottom_margin - text_h

    # KUTU KALDIRILDI (eski: draw.rectangle ile dolgun siyah dikdörtgen).
    # Artık sadece kalın siyah kontur/gölge ile okunabilirlik sağlanıyor
    # - kazanan manuel kapaktaki gibi, görsel çok daha temiz duruyor.
    stroke_offsets = [(-4, -4), (-4, 0), (-4, 4), (0, -4), (0, 4),
                      (4, -4), (4, 0), (4, 4), (-3, -3), (3, 3), (-3, 3), (3, -3)]
    for dx, dy in stroke_offsets:
        draw.multiline_text((x + dx, y + dy), wrapped, font=font,
                             fill=(0, 0, 0, 255), spacing=12)
    text_color = random.choice(TEXT_COLOR_PALETTE)
    draw.multiline_text((x, y), wrapped, font=font, fill=(*text_color, 255), spacing=12)

    draw_border(draw, img.width, img.height)

    img.save(out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--titles", required=True, help="generate_titles.py çıktısı")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--script", required=False, default="script.md",
                         help="Kapak konsepti için bağlam olarak kullanılacak script dosyası")
    parser.add_argument("--trends", required=False, default="trend.json",
                         help="trend_analysis.py çıktısı")
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
            print(f"  {len(trend_thumbnail_urls)} gerçek kapaktan (rakip/viral) "
                  f"stil paterni çıkarıldı: {style_summary[:100]}...")
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
                 concept.get("emphasis_target", ""),
                 concept.get("use_emphasis_ring", False),
                 final_path)
    print(f"Kapak hazır -> {final_path}  "
          f"(hook: \"{concept['hook_text']}\", "
          f"vurgu halkası: {concept.get('use_emphasis_ring', False)}, "
          f"başlık: \"{title}\")")


if __name__ == "__main__":
    main()
