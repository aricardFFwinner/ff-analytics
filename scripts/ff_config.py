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

# リーグルール: Week5終了まではNFL1年目の選手(ドラフト外含む)を常に1人以上ロスターに保持
ROOKIE_RULE_UNTIL_WEEK = 5
ROOKIE_MIN_COUNT = 1

# ==================================================================
# P1.5 機会指標(opportunity.py)の閾値。運用しながらここを調整する
# ==================================================================
EARLY_MODE_UNTIL_WEEK = 2   # データがこの週以下ならアーリーモード(単週判定)
OPP_RECENT_WEEKS = 2        # 通常モードで機会指標を平均する週数
PPG_RECENT_WEEKS = 3        # 実績PPGの参照週数

# 「スタメン級」とみなすポジション内順位(6人リーグ: 先発数+FLEX配分で概算)
STARTABLE_RANK = {"QB": 6, "RB": 15, "WR": 18, "TE": 8}

BLOWOUT_MARGIN = 21         # この点差以上の試合週は⚠️売り時判定の系列から除外

# --- 🔥/👀 ブレイク前夜(通常モード、直近平均) ---
FIRE_WRTE = {"ts": 0.20, "wopr": 0.50}          # ターゲットシェア / WOPR
WATCH_WRTE = {"ts": 0.15, "wopr": 0.40}
FIRE_RB = {"wtd": 12.0, "snap": 0.55}           # 加重機会(キャリー+1.5×タゲ) / スナップ率
WATCH_RB = {"wtd": 9.0, "snap": 0.45}

# --- 🔥E/👀E アーリーモード(単週、約1割増し+スナップ条件) ---
FIRE_WRTE_E = {"ts": 0.22, "wopr": 0.55, "snap": 0.65}
WATCH_WRTE_E = {"ts": 0.16, "wopr": 0.45, "snap": 0.50}
FIRE_RB_E = {"wtd": 13.0, "snap": 0.60}
WATCH_RB_E = {"wtd": 10.0, "snap": 0.50}

RZ_PROMOTE_COUNT = 2        # 直近2週のRZ機会がこの数以上なら👀→🔥に昇格

# 🔥/👀の対象ポジション(QBは先発なら常にスナップ100%でノイズになるため除外。
# QBは💎とドロップ判定のみ対象)
FIRE_WATCH_POSITIONS = {"RB", "WR", "TE"}

# --- ⚠️ 売り時(自軍) ---
SELL_DECLINE_RATIO = 0.20   # 3データ点前と比べた相対低下幅

# --- 💎 放置バリューの機会裏付け(フロック除外の下限) ---
DIAMOND_MIN_GAMES = 2       # 💎に必要な最低出場試合数(1週の爆発はフロックと区別不能)

# ==================================================================
# P2 ダッシュボード(dashboard.py / simulate.py)
# ==================================================================
# モンテカルロ試行回数
MC_SIMS = 10000

# ポジション別の週次得点の標準偏差(スタメン枠1つあたり)。
# 出典: 2025年実データのスタメン級選手(QB上位9/RB22/WR27/TE12)の週次PPR標準偏差の平均
#       (2026-08-17算出。K=FG3点+PAT1点近似、DSTはばらつきが大きい定説値)
POS_WEEKLY_SD = {
    "QB": 8.2, "RB": 8.4, "WR": 8.3, "TE": 7.4,
    "FLEX": 8.3, "K": 3.9, "D/ST": 7.0,
}

# プレーオフ設定のフォールバック(ESPN設定が取れない場合に使用)
PLAYOFF_TEAMS_FALLBACK = 4
DIAMOND_BACKING = {
    "WR": {"ts": 0.15, "snap": 0.55},
    "TE": {"ts": 0.13, "snap": 0.50},
    "RB": {"wtd": 9.0, "snap": 0.45},
    "QB": {"snap": 0.90},
}
