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

# Kanal logosunun rengi (sarı-yeşil/lime) - çerçeve için kullanılıyor.
BORDER_COLOR = (205, 220, 57)
BORDER_WIDTH = 4  # piksel

TEXT_COLOR_PALETTE = [
    (255, 255, 255),  # beyaz - klasik, her zaman okunaklı
    (255, 214, 0),    # sarı - dikkat çekici, gizem/uyarı hissi
]
# NOT: Kırmızı BİLEREK metin paletinden çıkarıldı - artık EMPHASIS_COLOR
# ile aynı renk olurdu (vurgu halkası da kırmızı), bu da metin ile
# vurgu halkasının görsel olarak birbirine karışmasına/çakışmasına
# yol açıyordu (VidIQ kapak puanı bunu doğruladı: 26/100). Artık net
# bir hiyerarşi var: KIRMIZI = "buraya bak" (nesne vurgusu),
# BEYAZ/SARI = "bunu oku" (metin) - ikisi asla aynı anda kırmızı olmaz.

# Kırmızı vurgu elementinin (halka/ok) rengi - nesnenin kritik
# noktasını işaret eder, izleyicinin bakışını konuya kilitler.
EMPHASIS_COLOR = (230, 30, 30)

MAX_TREND_TITLES = 8  # prompt'a en fazla kaç trend başlığı eklensin
MAX_TREND_THUMBNAILS = 3  # görsel patern analizine en fazla kaç gerçek kapak verilsin


def call_gemini(client, contents, max_tokens=600):
    """Gemini'ye istek atar (metin ya da vision, contents string ya da
    liste olabilir); geçici hatalarda üstel bekleme ile otomatik olarak
    yeniden dener. Gemini SDK'sının spesifik hata sınıfları garanti
    belgelenmediği için BİLEREK geniş bir Exception yakalaması
    kullanılıyor - her hata türünde yeniden denenir."""
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
    """trend_analysis.py çıktısından, en çok izlenen videoların GERÇEK
    kapak görseli URL'lerini çıkarır (en fazla MAX_TREND_THUMBNAILS
    kadar). Dosya yoksa/boşsa/URL yoksa boş liste döner - pipeline
    kırılmaz, çağıran taraf stil analizini atlar."""
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
    """Nişte gerçekten yüksek performans göstermiş kapakları Gemini'ye
    (vision) gösterip SADECE soyut stil/kompozisyon paternini (kontrast,
    renk paleti, konu yerleşimi, ışık) çıkarttırır. Gemini'ye AÇIKÇA
    hiçbir görseli/nesneyi/logoyu/kişiyi birebir tarif etmemesi, sadece
    genel eğilimi özetlemesi söylenir - bu, üretilecek YENİ görsele
    kopya değil sadece stil sinyali olarak yansıtılır (tıpkı trend
    başlıklarının kullanılma şekli gibi). Görsellerden biri/hepsi
    indirilemezse ya da analiz başarısız olursa None döner - kapak
    üretimi bu referans olmadan normal şekilde devam eder."""
    if not thumbnail_urls:
        return None

    image_parts = []
    for url in thumbnail_urls:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            image_parts.append(types.Part.from_bytes(data=resp.content, mime_type="image/jpeg"))
        except Exception:
            continue  # bu görsel indirilemedi, diğerleriyle devam

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


def load_trend_titles(trends_path: str) -> list:
    """trend_analysis.py çıktısından SADECE başlıkları (görsel/link
    değil) çıkarır - en çok izlenen ilk MAX_TREND_TITLES kadarını.
    Dosya yoksa ya da okunamazsa boş liste döner, pipeline kırılmaz."""
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
                                trend_titles: list, style_summary: str = None) -> dict:
    """
    Başlıktan BAĞIMSIZ bir kapak konsepti üretir: bir görsel açıklama
    (image prompt), çok kısa bir kışkırtıcı metin (hook text) VE kırmızı
    vurgu halkasının nereye çizileceğini belirleyen bir hedef tarifi.
    """
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
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Güvenli düşüş: jenerik bir konsept
        return {
            "visual_prompt": "a mysterious object under dramatic lighting, "
                              "close-up on one strange unexplained detail",
            "hook_text": "NEVER EXPLAINED",
            "emphasis_target": "the most visually distinctive detail in the center of the frame",
        }


def _extract_gemini_image_bytes(response):
    """Gemini yanıtındaki content parts içinden ilk görsel veriyi
    çıkarır. Bulamazsa None döner."""
    try:
        for part in response.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
    except (AttributeError, IndexError):
        pass
    return None


def generate_background(client, prompt: str, out_path: str):
    """Kapak arka planını Gemini (gemini-3-pro-image / Nano Banana Pro)
    ile üretir. Marka/telif ve metin güvenliği için prompt'a açık bir
    kısıt eklenir (BRAND_SAFETY_INSTRUCTION). Üretim başarısız olursa
    (güvenlik reddi, boş yanıt vb.) jenerik/soyut bir tarifle bir kez
    daha denenir - pipeline kırılmaz."""
    full_prompt = f"{THUMBNAIL_STYLE}. Subject: {prompt}.{BRAND_SAFETY_INSTRUCTION}"

    def _call(p: str):
        response = client.models.generate_content(
            model=MODEL_IMAGE,
            contents=p,
            config=types.GenerateContentConfig(
                response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
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
    """Dört kenara, kanal logosunun renginde ince bir çerçeve çizer
    (marka tutarlılığı için)."""
    for i in range(BORDER_WIDTH):
        draw.rectangle(
            [i, i, img_w - 1 - i, img_h - 1 - i],
            outline=BORDER_COLOR,
        )


def locate_emphasis_point(client, image_path: str, emphasis_target: str,
                           img_w: int, img_h: int):
    """Gemini'ye (vision) üretilen görseli gösterip emphasis_target'ın
    TAM piksel koordinatını ve yaklaşık boyutunu sordurur. İki deneme
    hakkı var (geçici hata/parse sorunu için). İkisi de başarısız
    olursa None döner - çağıran taraf SABİT/ALAKASIZ bir yere halka
    çizmek yerine halkayı TAMAMEN ATLAR (yanlış yere vurgu, hiç vurgu
    olmamasından daha kötü olur)."""
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
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)

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
    """Nesnenin kritik noktasının etrafına kırmızı, hafif kalın bir
    vurgu halkası çizer - izleyicinin bakışını konuya kilitler."""
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

    # Vurgu halkası ÖNCE eklenir ki metin kutusu onun üstünde net kalsın.
    # Nokta bulunamazsa (None) halka HİÇ ÇİZİLMEZ - alakasız/yanlış bir
    # yere halka koymaktansa temiz kapak tercih edilir.
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
        wrapped = textwrap.fill(short_text, width=wrap_width)

        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=10)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w <= max_text_width and text_h <= max_text_height:
            break
        font_size -= 4
    else:
        font = ImageFont.truetype(FONT_PATH, min_font_size)
        wrapped = textwrap.fill(short_text, width=10)
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

    # En son, her şeyin üstüne kanal renginde ince çerçeve
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

    concept = generate_thumbnail_concept(client, title, script_excerpt, trend_titles, style_summary)

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
