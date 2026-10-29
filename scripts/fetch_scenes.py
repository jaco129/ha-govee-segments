#!/usr/bin/env python3
"""Regenerate custom_components/govee_segments/scenes/<SKU>.json from Govee's effect library.

The library is public (no account) and is what the Govee Home app shows as scenes.
Only general-purpose categories are kept; branded tie-in packs are skipped.

Usage: python3 scripts/fetch_scenes.py H7094 [saved_response.json]
Standard library only.
"""
import json
import pathlib
import sys
import urllib.request

URL = "https://app2.govee.com/appsku/v1/light-effect-libraries?sku={sku}"
HEADERS = {"AppVersion": "6.4.10", "User-Agent": "GoveeHome/6.4.10"}
CATEGORIES = ("Natural", "Festival", "Life", "Emotion")
OUT_DIR = pathlib.Path(__file__).parents[1] / "custom_components" / "govee_segments" / "scenes"


def main() -> None:
    sku = sys.argv[1]
    if len(sys.argv) > 2:
        raw = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
    else:
        req = urllib.request.Request(URL.format(sku=sku), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.load(resp)
    scenes, seen = [], {}
    for category in raw["data"]["categories"]:
        if category["categoryName"] not in CATEGORIES:
            continue
        for scene in category["scenes"]:
            for effect in scene["lightEffects"]:
                base = scene["sceneName"].strip()
                seen[base] = seen.get(base, 0) + 1
                name = base if seen[base] == 1 else f"{base} {seen[base]}"
                scenes.append({"name": name, "code": effect["sceneCode"], "param": effect.get("scenceParam") or ""})
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{sku}.json"
    out.write_text(json.dumps(scenes, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(scenes)} scenes to {out}")


if __name__ == "__main__":
    main()
