# -*- coding: utf-8 -*-
"""2026年トランザクションの自動記録(GM行動パターン分析の素材集め)。

ESPNの活動フィードは過去に遡れなくなる可能性があるため、毎実行時に
最新分を docs/data/transactions.json へ追記していく(重複は排除)。
分析本体はシーズン中盤以降(データが溜まってから)。
"""
import json
import os

DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
TX_PATH = os.path.join(DOCS, "data", "transactions.json")


def record(snapshot):
    """snapshot["recent_activity"]をログへ追記。追加件数を返す。"""
    acts = snapshot.get("recent_activity") or []
    if not acts:
        return 0
    try:
        with open(TX_PATH, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        log = {"entries": []}
    seen = {(e.get("date"), e.get("team"), e.get("action"), e.get("player"))
            for e in log["entries"]}
    added = 0
    for a in acts:
        key = (a.get("date"), a.get("team"), a.get("action"), a.get("player"))
        if key in seen:
            continue
        seen.add(key)
        log["entries"].append(a)
        added += 1
    if added:
        log["entries"].sort(key=lambda e: e.get("date") or 0)
        os.makedirs(os.path.dirname(TX_PATH), exist_ok=True)
        with open(TX_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
    print(f"[info] トランザクション記録: 新規{added}件 / 累計{len(log['entries'])}件")
    return added
