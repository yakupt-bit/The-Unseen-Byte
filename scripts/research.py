"""
Haftalık konu araştırması yapar, Claude API'ye prompts/research_prompt.md
şablonunu gönderir ve sonucu facts.json olarak kaydeder.

Elle bir TOPIC_HINT verilmezse, niche.md'deki 4 alt-nişten GÜNE GÖRE
DÖNEREK biri otomatik seçilir.

GERÇEK KONU TEKRARI ÖNLEME: used_topics.json dosyası (repo kökünde,
her çalıştırma sonunda güncellenip commit edilir) o ana kadar işlenen
TÜM spesifik konuların bir listesini tutar. Her yeni araştırmada bu
liste Claude'a "bunları TEKRAR ETME" diye verilir - böylece sadece
4 kategori arasında değil, KATEGORİ İÇİNDE de aynı spesifik konu
(örn. "ESRB skandalı") ikinci kez seçilmez.

Kullanım:
    python scripts/research.py --out facts.json
"""
import argparse
import datetime
import json
import os

import anthropic

SUB_NICHES = [
    "Gaming Psychology - how games affect the brain, addiction science, competition and reward mechanics",
    "Tech & Gaming History - forgotten hardware/software stories, cancelled projects, unknown industry turning points",
    "Industry Secrets - behind-the-scenes of gaming/tech companies, production processes, lesser-known decisions",
    "Hardware & Science Myths - hardware legends, technical misconceptions, debunking or confirming with science",
]

USED_TOPICS_FILE = "used_topics.json"
MAX_TOPICS_IN_PROMPT = 40


def pick_sub_niche() -> str:
    idx = datetime.date.today().toordinal() % len(SUB_NICHES)
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

    topic_hint = os.environ.get("TOPIC_HINT", "").strip()
    if not topic_hint:
        topic_hint = pick_sub_niche()
        print(f"Konu ipucu verilmedi, otomatik alt-niş seçildi: {topic_hint}")

    used_topics = load_used_topics()
    print(f"Şu ana kadar işlenen konu sayısı: {len(used_topics)}")

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
