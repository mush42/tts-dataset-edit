# coding: utf-8

import json
from pathlib import Path
from thefuzz.process import extractBests


JSON_FILE = Path(r"C:\Users\ibnom\Kareem-voice\metadata.edited.json.back")

DIACS = str.maketrans("", "", "ًٌٍَُِّْ")
def clean_diacritics(s):
    return s.translate(DIACS)

sents = {
    item["idx"]: clean_diacritics(item["text"])
    for item in json.loads(JSON_FILE.read_text(encoding="utf-8"))
    if not item.get("deleted")
}

outputs = []
for item in sents.values():
    outputs.append(
        extractBests(item, sents, score_cutoff=95, limit=3)
    )

for idx,  matches in enumerate(outputs):
    if len(matches) > 1:
        print(f"#{idx}")
        print(list(matches))

    