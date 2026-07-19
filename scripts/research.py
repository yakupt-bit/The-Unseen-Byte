"""
Haftalık konu araştırması yapar, Claude API'ye prompts/research_prompt.md
şablonunu gönderir ve sonucu facts.json olarak kaydeder.

Kullanım:
    python scripts/research.py --out facts.json
"""
import argparse
import json
import os

import anthropic


def load_prompt(topic_hint: str) -> str:
    with open("prompts/research_prompt.md", "r", encoding="utf-8") as f:
        template = f.read()
    return template.replace("{TOPIC_HINT}", topic_hint or "genel teknoloji/oyun kültürü")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    topic_hint = os.environ.get("TOPIC_HINT", "")
    prompt = load_prompt(topic_hint)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    # Model'in ```json fence eklemesi ihtimaline karşı temizle
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Parse edilemezse ham metni de sakla, insan issue'da görür
        data = {"raw": cleaned}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Araştırma tamamlandı -> {args.out}")


if __name__ == "__main__":
    main()
