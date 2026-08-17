# -*- coding: utf-8 -*-
"""リーグ固有の設定。"""

MY_TEAM_ID = 1
SEASON = 2026

# スタメン構成
STARTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,   # RB/WR/TE
    "D/ST": 1,
    "K": 1,
}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}

# レギュラーシーズン最終週 / チャンピオンシップ週
REGULAR_SEASON_END = 14
CHAMPIONSHIP_WEEKS = [15, 16, 17]

# ESPN略称 → nflverse略称
ESPN_TO_NFLVERSE = {"LAR": "LA", "WSH": "WAS"}

# 出場が危ぶまれる状態(スタメン推奨から除外 or 警告)
INJURY_OUT = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
INJURY_RISK = {"DOUBTFUL", "QUESTIONABLE"}

# 「僅差」とみなすポイント差(AI相談を推奨するライン)
CLOSE_CALL_MARGIN = 2.0
