"""
Haftalık konu araştırması yapar, Claude API'ye prompts/research_prompt.md
şablonunu gönderir ve sonucu facts.json olarak kaydeder.

CANLI WEB ARAMASI (grounding): Claude artık facts.json üretirken
GERÇEK ZAMANLI web araması yapabiliyor (Anthropic'in web_search tool'u
etkin). Bu, script'in kendi hafızasından üretilmiş - potansiyel olarak
hatalı/uydurma - kaynaklara güvenme riskini azaltır; Claude artık
iddia ettiği kitap/makale/tarih gibi detayları gerçekten arayıp
doğrulayabiliyor. Arama sonuçları aynı API çağrısı içinde otomatik
işleniyor, ekstra bir kod döngüsüne gerek yok.

Elle bir TOPIC_HINT verilmezse, aşağıdaki 18 alt-nişten (6 ana kategori,
her birinde 3 alt-niş) biri, ÜRETİLEN VİDEO SAYISINA göre sırayla
seçilir - böylece takvim günü atlasa da (haftada 2 gün yayın) rotasyon
düzgün ilerler, gün bazlı değildir.

--- BUGÜNKÜ KRİTİK BUG DÜZELTMESİ (SUB_NICHES - "Hardware & Science
Myths" kategorisi çöküyordu) ---
"Engineering Myths" ve "Thermal & Performance Science" alt-niş
tanımları GAMING bağlamını hiç içermiyordu ("gaming" kelimesi hiç
geçmiyordu). Bu yüzden bu alt-niş seçildiğinde research.py genel
PC/veri merkezi/endüstriyel donanım konuları üretiyordu (ör. "AI Data
Centers", "nanometre pazarlama yalanı") - niche_check.py bunları HER
SEFERİNDE "gaming endüstrisiyle ilgisi yok" diye reddediyordu. Sonuç:
bu alt-niş seçildiğinde MAX_ATTEMPTS(5) hakkının hepsi bu tuzağa
harcanıyor ve TÜM RUN ÇÖKÜYORDU (gerçek bir prodüksiyon run'ında
gözlemlendi). Artık her iki tanım da AÇIKÇA "gaming hardware/consoles"
bağlamına sabitlendi - niche_check ile artık uyumlu olmalı.

RETRY_OFFSET ortam değişkeni verilirse (workflow'daki yeniden deneme
döngüsü tarafından ayarlanır), rotasyonu o kadar ileri kaydırır - böylece
niş kontrolü başarısız olup yeniden denendiğinde FARKLI bir alt-niş
seçilir, aynı reddedilen konu tekrar denenmez.

GERÇEK KONU TEKRARI ÖNLEME: used_topics.json dosyası (repo kökünde,
her çalıştırma sonunda güncellenip commit edilir) o ana kadar işlenen
TÜM spesifik konuların bir listesini tutar. Her yeni araştırmada bu
liste Claude'a "bunları TEKRAR ETME" diye verilir - böylece sadece
kategori arasında değil, KATEGORİ İÇİNDE de aynı spesifik konu
(örn. "ESRB skandalı") ikinci kez seçilmez.

Kullanım:
    python scripts/research.py --out facts.json
"""
import argparse
import json
import os

import anthropic

SUB_NICHES = [
    # --- Gaming Psychology ---
    "The Reward Loop - how games use dopamine, variable rewards, and near-miss mechanics to keep players engaged",
    "Fear & Frustration Design - how games intentionally use difficulty and controlled frustration to create memorable experiences",
    "Social & Identity Play - how multiplayer games shape identity, tribalism, and social belonging",
    # --- Tech & Gaming History ---
    "Forgotten Hardware - cancelled consoles, prototype devices, and hardware that almost changed everything",
    "Turning Point Decisions - the exact moments in gaming history that redirected the entire industry",
    "Software Archaeology - abandoned engines, early prototypes, and the technical history behind iconic games",
    # --- Industry Secrets ---
    "Studio Behind-the-Scenes - internal decisions, crunch culture, and the real production stories behind games",
    "Corporate Strategy - business decisions and rivalries that shaped the gaming industry",
    "Marketing & Launch Secrets - how games are actually marketed, hyped, and sometimes deceptively sold",
    # --- Hardware & Science Myths (bugün gaming'e AÇIKÇA sabitlendi) ---
    "Gaming Hardware Engineering Myths - popular beliefs about gaming PCs, consoles, and controllers that are wrong, half-true, or verified by real engineering (must stay tied to gaming/console hardware, not general enterprise tech)",
    "Gaming Console & PC Thermal Science - the real science behind overheating, throttling, and hardware limits specifically in gaming consoles and gaming PCs (not data centers or general computing)",
    "Signal & Data Mysteries - how data, networking, and signal processing actually work inside gaming hardware (controllers, online multiplayer, matchmaking servers)",
    # --- Esports & Competitive Culture ---
    "Pro Player Psychology - the mental training, burnout, and competitive mindset of professional gamers",
    "Tournament Controversies - cheating scandals and behind-the-scenes esports drama",
    "Competitive Meta Evolution - how competitive strategies and metas evolve and get discovered",
    # --- Digital Preservation & Lost Media ---
    "Lost & Cancelled Games - games that were finished or nearly finished but never released",
    "Game Archaeology - how preservationists recover, restore, and document dying digital history",
    "Abandoned Prototypes - leaked builds, beta versions, and the untold stories behind them",
]

USED_TOPICS_FILE = "used_topics.json"
MAX_TOPICS_IN_PROMPT = 40

# Anthropic'in sunucu tarafı web arama aracı - Claude prompt'u işlerken
# gerekli gördüğü aramaları otomatik yapar, sonuçları aynı yanıt
# içinde döndürür (ekstra bir döngü/kod gerekmez).
WEB_SEARCH_TOOL = [{"type": "web_search_20250305", "name": "web_search"}]

GROUNDING_INSTRUCTION = (
    "\n\nÖNEMLİ - GERÇEK KAYNAK DOĞRULAMA: Bir iddia, tarih, kitap adı, "
    "yayın yılı veya istatistik kullanacaksan, bunu kendi hafızandan "
    "UYDURMADAN ÖNCE web araması yaparak gerçekten var olduğunu ve "
    "doğru olduğunu doğrula. Emin olmadığın veya aramada teyit "
    "edemediğin spesifik bir detay (ör. bir kitabın tam yayın yılı, "
    "bir makalenin tam başlığı) varsa, o detayı ÇIKAR veya daha genel "
    "bir ifadeyle değiştir - kesinlikle uydurma bir kaynak gösterme. "
    "Doğrulanmış, gerçek kaynaklar kullanılmış bir konu, hiç "
    "kaynaklanmamış ama iddialı bir konudan her zaman daha değerlidir."
)

NICHE_ANCHOR_INSTRUCTION = (
    "\n\nÖNEMLİ - NİŞ SABİTLEME: Bu kanal SADECE gaming/video oyunları "
    "endüstrisi, teknolojisi ve kültürüyle ilgili içerik üretir. Konu "
    "başlığında 'hardware', 'engineering', 'thermal', 'science' gibi "
    "genel teknik kelimeler geçse bile, ele aldığın konu MUTLAKA "
    "video oyunları/gaming konsolları/gaming PC'leri bağlamında "
    "kalmalı - genel kurumsal teknoloji (ör. veri merkezleri, "
    "kurumsal sunucular, genel yarı iletken pazarlaması) KESİNLİKLE "
    "ELE ALINMAMALI, bu niş dışı sayılır ve reddedilir."
)


def pick_sub_niche(used_topics_count: int, retry_offset: int = 0) -> str:
    idx = (used_topics_count + retry_offset) % len(SUB_NICHES)
    return SUB_NICHES[idx]


def load_used_topics() -> list:
    if not os.path.exists(USED_TOPICS_FILE):
        return []
    with open(USED_TOPICS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_used_topics(topics: list):
    with open(USED_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)


def load_prompt(topic_hint: str, used_topics: list) -> str:
    with open("prompts/research_prompt.md", "r", encoding="utf-8") as f:
        template = f.read()
    prompt = template.replace("{TOPIC_HINT}", topic_hint)

    if used_topics:
        recent = used_topics[-MAX_TOPICS_IN_PROMPT:]
        avoid_block = (
            "\n\nÖNEMLİ - TEKRAR ETME: Aşağıdaki konular DAHA ÖNCE işlendi, "
            "bunların aynısını veya çok benzerini SEÇME, tamamen farklı, "
            "yeni bir açı/konu bul:\n"
            + "\n".join(f"- {t}" for t in recent)
        )
        prompt += avoid_block

    prompt += NICHE_ANCHOR_INSTRUCTION
    prompt += GROUNDING_INSTRUCTION

    return prompt


def extract_text(response) -> str:
    """Yanıttaki TÜM metin bloklarını birleştirir. Web araması
    kullanıldığında yanıt, arama sorguları/sonuçları için ek content
    bloklarıyla (tool_use, web_search_tool_result) birlikte geliyor -
    sadece type=='text' olan blokları alıp birleştirmek, aramanın
    ürettiği ekstra blokları otomatik olarak es geçer."""
    return "".join(
        block.text for block in response.content if block.type == "text"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    used_topics = load_used_topics()
    print(f"Şu ana kadar işlenen konu sayısı: {len(used_topics)}")

    topic_hint = os.environ.get("TOPIC_HINT", "").strip()
    if not topic_hint:
        retry_offset = int(os.environ.get("RETRY_OFFSET", "0"))
        topic_hint = pick_sub_niche(len(used_topics), retry_offset)
        print(f"Konu ipucu verilmedi, otomatik alt-niş seçildi: {topic_hint}"
              + (f" (deneme #{retry_offset + 1})" if retry_offset else ""))

    prompt = load_prompt(topic_hint, used_topics)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,  # web araması ek content bloğu ürettiği için yükseltildi
        tools=WEB_SEARCH_TOOL,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = extract_text(response)

    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {"raw": cleaned}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    new_topic = data.get("topic", "").strip()
    if new_topic:
        used_topics.append(new_topic)
        save_used_topics(used_topics)
        print(f"Yeni konu listeye eklendi: {new_topic}")

    print(f"Araştırma tamamlandı -> {args.out}")


if __name__ == "__main__":
    main()
