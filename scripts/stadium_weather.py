# -*- coding: utf-8 -*-
"""会場情報と天候予報(Open-Meteo、無料・認証不要)。

屋外試合のみキックオフ時刻の予報を取得。ドーム/屋根閉は天候不要。
中立地(海外開催など)は座標が不明なためスキップする。
"""
import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")

# ホームチーム(nflverse略称) → スタジアム座標
STADIUM_COORDS = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7550, -84.4010), "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870), "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167),
    "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995), "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373),
    "KC": (39.0489, -94.4839), "LA": (33.9535, -118.3392), "LAC": (33.9535, -118.3392),
    "LV": (36.0909, -115.1833), "MIA": (25.9580, -80.2389), "MIN": (44.9735, -93.2575),
    "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316), "SF": (37.4030, -121.9700), "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713), "WAS": (38.9076, -76.8645),
}

INDOOR_ROOFS = {"dome", "closed"}

_forecast_cache = {}


def kickoff_datetimes(gameday: str, gametime: str):
    """gameday(YYYY-MM-DD)+gametime(HH:MM, ET) → (ET aware dt, JST aware dt)。"""
    if not gameday or not gametime:
        return None, None
    try:
        dt_et = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        return dt_et, dt_et.astimezone(JST)
    except ValueError:
        return None, None


def kickoff_jst_label(gameday: str, gametime: str) -> str:
    _, jst = kickoff_datetimes(gameday, gametime)
    if not jst:
        return ""
    wd = "月火水木金土日"[jst.weekday()]
    return f"{wd} {jst.strftime('%H:%M')}"


def weather_for_game(home_team: str, gameday: str, gametime: str, roof: str, neutral: bool):
    """屋外試合の天候文字列と警告フラグを返す。(weather_str, wind_warn)"""
    roof_l = (roof or "").lower()
    if roof_l in INDOOR_ROOFS:
        return "ドーム", False
    if neutral:
        return "中立地", False
    coords = STADIUM_COORDS.get(home_team)
    dt_et, _ = kickoff_datetimes(gameday, gametime)
    if not coords or not dt_et:
        return "", False

    lat, lon = coords
    key = (round(lat, 2), round(lon, 2), gameday)
    try:
        if key not in _forecast_cache:
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                   f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
                   f"&wind_speed_unit=ms&timezone=America%2FNew_York"
                   f"&start_date={gameday}&end_date={gameday}")
            with urllib.request.urlopen(url, timeout=30) as r:
                _forecast_cache[key] = json.loads(r.read().decode())
        data = _forecast_cache[key]
        hours = data["hourly"]["time"]
        target = dt_et.strftime("%Y-%m-%dT%H:00")
        idx = hours.index(target) if target in hours else None
        if idx is None:
            return "", False
        temp = data["hourly"]["temperature_2m"][idx]
        rain = data["hourly"]["precipitation_probability"][idx]
        wind = data["hourly"]["wind_speed_10m"][idx]
        wind_warn = wind is not None and wind >= 8.0
        parts = []
        if temp is not None:
            parts.append(f"{round(temp)}°C")
        if wind is not None:
            parts.append(f"風{round(wind)}m/s")
        if rain is not None:
            parts.append(f"雨{round(rain)}%")
        return " ".join(parts), wind_warn
    except Exception as e:
        print(f"[warn] 天候取得失敗 {home_team} {gameday}: {e}")
        return "", False
