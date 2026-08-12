"""
Yayın öncesi niş doğrulama + konu tekrarı kontrolü.

Amaç: script.md ve title.json üretildikten sonra, içeriğin kanalın
niş tanımına (prompts/niche.md) gerçekten uyup uymadığını VE daha önce
işlenmiş bir konunun (used_topics.json) tekrarı olup olmadığını ayrı
ayrı Claude çağrılarıyla kontrol eder. İkisinden biri başarısız olursa
süreç DURDURULUR - voiceover, görsel üretimi, upload gibi pahalı/geri
alınamaz adımlara hiç geçilmez.

Kullanım:
    python scripts/niche_check.py --script script.md --titles titles.json

Çıkış kodu:
    0 -> niş uyumlu VE tekrar değil, workflow devam edebilir
    1 -> uyumsuz VEYA tekrar, workflow burada durmalı

--- DEĞİŞİKLİK GEÇMİŞİ (bugünkü düzeltmeler) ---
1. BUG DÜZELTİLDİ: titles_data içinden başlık artık doğru okunuyor.
   Eskiden "selected_title"/"title" anahtarları aranıyordu ama
   generate_titles.py'nin gerçek çıktı formatı {"selected": [...]}.
   Bu yüzden niş kontrolü şimdiye kadar HİÇBİR ZAMAN gerçek başlığı
   görmedi, ham JSON'un string halini görüyordu.
2. YENİ: check_duplicate_topic() eklendi - used_topics.json'daki
   geçmiş konularla yeni script'i karşılaştırıp GERÇEKTEN aynı olayı/
   konuyu mu anlattığını ayrı bir Claude çağrısıyla soruyor. Eskiden
   bu kontrol SADECE research.py'deki prompt seviyesinde bir "rica"
   idi (Claude'a "tekrar etme" deniyordu ama kod seviyesinde
   doğrulanmıyordu) - artık kod seviyesinde de zorunlu bir kapı var.
"""
import argparse
import json
import os
import sys
import time

import anthropic

MODEL_UTILITY = "claude-haiku-4-5-20251001"
MAX_RETRIES = 4
RETRY_BASE_DELAY = 5

USED_TOPICS_FILE = "used_topics.json"
MAX_TOPICS_IN_DUPLICATE_CHECK = 40

RETRYABLE_EXCEPTIONS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
    anthropic.RateLimitError,
)


def load_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_used_topics() -> list:
    if not os.path.exists(USED_TOPICS_FILE):
        return []
    try:
        with open(USED_TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def extract_title(titles_data) -> str:
    """generate_titles.py'nin gerçek çıktı formatı: {"selected": ["...", ...]}.
    Eski kod yanlış anahtar isimleri arıyordu ve hep boşa düşüyordu -
    bu yüzden ham JSON'un str() hali "başlık" olarak kullanılıyordu.
    Bu fonksiyon doğru anahtarı okur, olmazsa eski yedekleri de dener,
    en son çare olarak str() yedeğine düşer (asla patlamaz)."""
    if isinstance(titles_data, dict):
        selected = titles_data.get("selected")
        if isinstance(selected, list) and selected:
            return str(selected[0])
        for key in ("selected_title", "title"):
            value = titles_data.get(key)
            if value:
                return str(value)
    return str(titles_data)


def call_claude(client, prompt, model=MODEL_UTILITY, max_tokens=400):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  UYARI: Claude API geçici hata ({type(e).__name__}), "
                      f"{delay}sn sonra tekrar deneniyor "
                      f"(deneme {attempt + 1}/{MAX_RETRIES})...")
                time.sleep(delay)
    raise last_error


def check_niche_fit(client, niche, title, script):
    prompt = f"""Sen bu YouTube kanalının editoryal bekçisisin. Görevin,
üretilen içeriğin kanalın nişine GERÇEKTEN uyup uymadığını sert bir
şekilde denetlemek. Amaç bekçilik yapmak, nazik olmak değil.

KANALIN NİŞ TANIMI:
{niche}

ÜRETİLEN BAŞLIK:
{title}

ÜRETİLEN SCRIPT (ilk 800 karakter):
{script[:800]}

Soru: Bu içerik, yukarıdaki niş tanımına GERÇEKTEN uyuyor mu?

Kurallar:
- Sadece yüzeysel bir kelime örtüşmesi ("hardware" gibi çift anlamlı
  kelimeler) yeterli değil - konunun ÖZÜ niş ile uyumlu olmalı.
- Niş "teknoloji/oyun donanımı tarihi" ise; nörobilim, psikoloji,
  insan biyolojisi, genel "self-help" gibi konular UYUMSUZ sayılır,
  başlıkta teknoloji kelimesi geçse bile.
- Şüphedeysen (yüzde 50-50 gibi duruyorsa) UYUMSUZ say - riski
  yayınlamamak yayınlamaktan daha ucuzdur.

Çıktı SADECE şu JSON formatında olsun, başka hiçbir şey yazma:
{{"fits_niche": true/false, "confidence": 1-10 arası tam sayı, "reason": "kısa gerekçe"}}
"""
    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Parse edilemezse güvenli tarafta kal: uyumsuz say, insan baksın
        return {"fits_niche": False, "confidence": 0,
                "reason": f"JSON parse edilemedi, ham yanıt: {raw[:200]}"}


def check_duplicate_topic(client, used_topics, title, script):
    """Yeni script'in, used_topics.json'daki GEÇMİŞ konulardan
    herhangi biriyle aynı olayı/konuyu anlatıp anlatmadığını kontrol
    eder. Bu, research.py'deki "tekrar etme" ricasının kod seviyesinde
    zorunlu bir doğrulaması - Claude prompt'ta rica edilse bile aynı
    konuyu (farklı bir başlık/açı altında) tekrar seçebiliyordu, artık
    bu durumda pipeline burada durur ve otomatik farklı bir alt-nişle
    yeniden dener (workflow'daki mevcut 5 denemelik retry döngüsü
    sayesinde).

    used_topics boşsa (ilk video ya da dosya yoksa) otomatik olarak
    "tekrar değil" der - kontrol edilecek bir şey yoktur.
    """
    if not used_topics:
        return {"is_duplicate": False, "confidence": 10,
                "reason": "used_topics.json boş, kontrol edilecek geçmiş konu yok."}

    recent = used_topics[-MAX_TOPICS_IN_DUPLICATE_CHECK:]
    topics_block = "\n".join(f"- {t}" for t in recent)

    prompt = f"""Sen bu YouTube kanalının tekrar-önleme denetçisisin.
Görevin, YENİ üretilen bir videonun, DAHA ÖNCE yayınlanmış videolardan
herhangi biriyle GERÇEKTEN AYNI OLAYI/KONUYU mu anlattığını tespit etmek.

ÖNEMLİ AYRIM: Aynı GENEL KATEGORİYE (örn. "1980'ler oyun tarihi") ait
olmak TEKRAR sayılmaz - asıl anlatılan SPESİFİK olay/olgu/karar aynıysa
tekrar sayılır. Örnek: "1983 video oyun çöküşü ve nedenleri" ile
"1983 çöküşü sonrası Nintendo'nun kilit çip sistemi" konuları farklı
görünse de, ikisi de aynı tarihsel olayı (1983 çöküşü) merkeze alıp
üst üste biniyorsa, izleyici için "aynı video" gibi hissettirir ve
TEKRAR sayılmalıdır.

DAHA ÖNCE İŞLENMİŞ KONULAR:
{topics_block}

YENİ ÜRETİLEN BAŞLIK:
{title}

YENİ ÜRETİLEN SCRIPT (ilk 1200 karakter):
{script[:1200]}

Soru: Bu yeni içerik, yukarıdaki geçmiş konulardan biriyle aynı
olayı/konuyu mu anlatıyor (tekrar), yoksa gerçekten farklı bir konu mu?

Şüphedeysen (yüzde 50-50 gibi duruyorsa) TEKRAR say - riski
yayınlamamak, izleyiciye aynı videoyu iki kez göstermekten daha ucuzdur.

Çıktı SADECE şu JSON formatında olsun, başka hiçbir şey yazma:
{{"is_duplicate": true/false, "confidence": 1-10 arası tam sayı, "reason": "kısa gerekçe, hangi geçmiş konuyla çakıştığını belirt"}}
"""
    raw = call_claude(client, prompt)
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Parse edilemezse güvenli tarafta kal: tekrar say, insan baksın
        return {"is_duplicate": True, "confidence": 0,
                "reason": f"JSON parse edilemedi, ham yanıt: {raw[:200]}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True, help="script.md yolu")
    parser.add_argument("--titles", required=True, help="titles.json yolu")
    args = parser.parse_args()

    niche = load_text("prompts/niche.md")
    script = load_text(args.script)

    with open(args.titles, "r", encoding="utf-8") as f:
        titles_data = json.load(f)
    title = extract_title(titles_data)
    print(f"Kontrol edilen başlık: {title}")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 1) Niş uyumu kontrolü
    fit_result = check_niche_fit(client, niche, title, script)
    print(f"Niş kontrolü sonucu: {json.dumps(fit_result, ensure_ascii=False)}")

    if not fit_result.get("fits_niche", False):
        print(f"\n❌ DURDURULDU: İçerik niş tanımına uymuyor.")
        print(f"   Gerekçe: {fit_result.get('reason', 'belirtilmemiş')}")
        print(f"   Güven skoru: {fit_result.get('confidence', '?')}/10")
        sys.exit(1)

    print(f"✅ Niş uyumlu (güven: {fit_result.get('confidence', '?')}/10).")

    # 2) Konu tekrarı kontrolü
    used_topics = load_used_topics()
    print(f"Geçmiş konu sayısı (tekrar kontrolü için): {len(used_topics)}")
    dup_result = check_duplicate_topic(client, used_topics, title, script)
    print(f"Tekrar kontrolü sonucu: {json.dumps(dup_result, ensure_ascii=False)}")

    if dup_result.get("is_duplicate", False):
        print(f"\n❌ DURDURULDU: İçerik daha önce işlenmiş bir konunun tekrarı.")
        print(f"   Gerekçe: {dup_result.get('reason', 'belirtilmemiş')}")
        print(f"   Güven skoru: {dup_result.get('confidence', '?')}/10")
        sys.exit(1)

    print(f"✅ Tekrar değil (güven: {dup_result.get('confidence', '?')}/10), devam ediliyor.")
    sys.exit(0)


if __name__ == "__main__":
    main()
