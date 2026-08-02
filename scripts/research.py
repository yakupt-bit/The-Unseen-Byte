"""
Haftalık konu araştırması yapar, Claude API'ye prompts/research_prompt.md
şablonunu gönderir ve sonucu facts.json olarak kaydeder.

Elle bir TOPIC_HINT verilmezse, aşağıdaki 18 alt-nişten (6 ana kategori,
her birinde 3 alt-niş) biri, ÜRETİLEN VİDEO SAYISINA göre sırayla
seçilir - böylece takvim günü atlasa da (haftada 3 gün yayın) rotasyon
düzgün ilerler, gün bazlı değildir.

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
    # --- Hardware & Science Myths ---
    "Engineering Myths - popular hardware beliefs that are wrong, half-true, or verified by real engineering",
    "Thermal & Performance Science - the real science behind overheating, throttling, and hardware limits",
    "Signal & Data Mysteries - how data, networking, and signal processing actually work inside gaming hardware",
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


def pick_sub_niche(used_topics_count: int) -> str:
    idx = used_topics_count % len(SUB_NICHES)
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

    return prompt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    used_topics = load_used_topics()
    print(f"Şu ana kadar işlenen konu sayısı: {len(used_topics)}")

    topic_hint = os.environ.get("TOPIC_HINT", "").strip()
    if not topic_hint:
        topic_hint = pick_sub_niche(len(used_topics))
        print(f"Konu ipucu verilmedi, otomatik alt-niş seçildi: {topic_hint}")

    prompt = load_prompt(topic_hint, used_topics)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

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
