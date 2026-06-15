# -*- coding: utf-8 -*-
"""
app.py — השרת המרכזי
---------------------
Flask הוא ספריית Python קטנה לבניית אתרים.

איך Flask עובד:
  @app.route("/") זה "decorator" — הוא אומר ל-Flask:
  "כשמישהו נכנס לכתובת /, הפעל את הפונקציה הזו".

  הפונקציה מחזירה HTML (דף) או JSON (נתונים לJavaScript).

הרצה:
  pip install flask
  python app.py
  ← פותח דפדפן על http://localhost:5000
"""

import os
import random
import socket
import time
import json as _json
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import quote
from flask import Flask, render_template, request, jsonify
from algorithm import run_algorithm, STATIONS, haversine

app = Flask(__name__)
random.seed(42)

# ── קובץ שמירה (שורד הפעלה מחדש של השרת) ──
DATA_FILE = os.path.join(os.path.dirname(__file__), "ev_data.json")

def _load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = _json.load(f)
            return d.get("occupancy", {}), d.get("drivers", {}), d.get("counter", 0)
    except Exception:
        return {}, {}, 0

def _save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            _json.dump({"occupancy": OCCUPANCY, "drivers": RECENT_DRIVERS,
                        "counter": _driver_counter[0]}, f, ensure_ascii=False)
    except Exception:
        pass

_occ, _drv, _cnt = _load_data()

# עומס מדווח על ידי משתמשים: {station_id: count}
OCCUPANCY = _occ

# נהגים אמיתיים שהשתמשו במערכת: {driver_id: {id, lat, lon, ts, ts_str, assignment}}
RECENT_DRIVERS = _drv
_driver_counter = [_cnt]

def _cleanup_drivers():
    cutoff = time.time() - 3600
    expired = [k for k, v in list(RECENT_DRIVERS.items()) if v["ts"] < cutoff]
    for k in expired:
        del RECENT_DRIVERS[k]

def get_active_drivers():
    _cleanup_drivers()
    return [
        {"id": v["id"], "lat": v["lat"], "lon": v["lon"],
         "ts_str": v["ts_str"], "stored_assignment": v.get("assignment")}
        for v in RECENT_DRIVERS.values()
    ]

# ──────────────────────────────────────────
# יצירת 30 נהגים אקראיים (seed קבוע = תמיד אותם נהגים)
# ──────────────────────────────────────────
DEMO_DRIVERS = [
    {"id": f"D{i+1}",
     "lat": round(random.uniform(31.74, 31.81), 4),
     "lon": round(random.uniform(35.16, 35.24), 4)}
    for i in range(30)
]

# ──────────────────────────────────────────
# דף נהג — /
# ──────────────────────────────────────────
@app.route("/")
def driver_page():
    """
    render_template טוען את קובץ HTML מתיקיית templates/
    ומאפשר להעביר אליו משתנים מפייתון (כאן: stations).
    """
    return render_template("index.html", stations=STATIONS)


# ──────────────────────────────────────────
# דף מנהל — /admin
# ──────────────────────────────────────────
@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# ──────────────────────────────────────────
# API: חיפוש עמדה לנהג בודד
# ──────────────────────────────────────────
@app.route("/api/find", methods=["POST"])
def api_find():
    """
    מקבל JSON מהדפדפן: { lat, lon, preference }
    מריץ את האלגוריתם עבור נהג בודד + כל שאר הנהגים הדמו
    מחזיר JSON עם הקצאה ורשימת עמדות
    """
    data = request.get_json()
    lat = float(data.get("lat", 31.775))
    lon = float(data.get("lon", 35.213))
    preference = data.get("preference", "any")

    # הנהג הנוכחי + נהגי הדמו
    # שמור נהג אמיתי — כל חיפוש = רשומה נפרדת
    _cleanup_drivers()
    _driver_counter[0] += 1
    driver_id = f"R{_driver_counter[0]:03d}"
    RECENT_DRIVERS[driver_id] = {
        "id": driver_id, "lat": lat, "lon": lon,
        "ts": time.time(),
        "ts_str": datetime.now().strftime("%H:%M"),
        "assignment": None,  # יתעדכן אחרי הרצת האלגוריתם
    }

    user_driver = {"id": "USER", "lat": lat, "lon": lon}
    all_drivers = [user_driver] + DEMO_DRIVERS

    result = run_algorithm(all_drivers, preference=preference, max_dist=5.0, occupancy_data=OCCUPANCY)

    # מצא את ההקצאה של הנהג הנוכחי
    user_assignment = result["assignments"].get("USER")

    # עמדות עם מרחק מהנהג — מסוננות לפי העדפה
    stations_with_dist = []
    for s in result["station_loads"]:
        if preference == "fast" and s["fast"] == 0:
            continue
        if preference == "slow" and s["slow"] == 0:
            continue
        dist = haversine(lat, lon, s["lat"], s["lon"])
        if dist <= 5.0:
            stations_with_dist.append({**s, "dist": round(dist, 2)})
    stations_with_dist.sort(key=lambda x: x["dist"])

    # שמור את ההקצאה האמיתית לתצוגה בדשבורד המנהל
    RECENT_DRIVERS[driver_id]["assignment"] = user_assignment
    _save_data()

    return jsonify({
        "assignment": user_assignment,
        "nearby_stations": stations_with_dist,
        "stats": result["stats"],
    })


# ──────────────────────────────────────────
# API: הרצת האלגוריתם לדשבורד מנהל
# ──────────────────────────────────────────
@app.route("/api/run", methods=["POST"])
def api_run():
    """
    מריץ את האלגוריתם על כל 30 נהגי הדמו.
    מחזיר תוצאות מלאות לדשבורד המנהל.
    """
    data = request.get_json() or {}
    preference = data.get("preference", "any")

    active = get_active_drivers()
    if not active:
        return jsonify({
            "no_active_drivers": True,
            "drivers": [], "station_loads": [], "assignments": {}, "unassigned": [],
            "stats": {
                "n_drivers": 0, "n_assigned": 0, "n_unassigned": 0,
                "n_stations": len(STATIONS), "n_full": 0, "avg_load": 0,
                "elapsed": 0, "iterations": 0,
            }
        })

    # הרץ אלגוריתם לסטטיסטיקות ועומס תחנות
    algo_drivers = [{"id": d["id"], "lat": d["lat"], "lon": d["lon"]} for d in active]
    result = run_algorithm(algo_drivers, preference=preference, max_dist=5.0, occupancy_data=OCCUPANCY)

    # החלף הקצאות מחושבות בהקצאות האמיתיות שכל נהג קיבל בפועל
    stored_map = {d["id"]: d for d in active}
    for d in result["drivers"]:
        stored = stored_map.get(d["id"], {})
        d["assignment"] = stored.get("stored_assignment")
        d["ts_str"] = stored.get("ts_str", "")

    n_assigned = sum(1 for d in result["drivers"] if d.get("assignment"))
    result["stats"]["n_assigned"] = n_assigned
    result["stats"]["n_unassigned"] = len(active) - n_assigned

    return jsonify(result)


# ──────────────────────────────────────────
# API: נתוני תחנות (לשימוש עתידי / ERD)
# ──────────────────────────────────────────
@app.route("/api/stations")
def api_stations():
    return jsonify(STATIONS)


# ──────────────────────────────────────────
# API: גיאוקודינג כתובת → קואורדינטות
# ──────────────────────────────────────────
@app.route("/api/geocode")
def api_geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "no query"}), 400

    # מוסיף ירושלים אם לא צוין
    if "ירושלים" not in q and "jerusalem" not in q.lower():
        q += ", ירושלים"

    url = "https://nominatim.openstreetmap.org/search?q=" + quote(q) + "&format=json&limit=1&countrycodes=il"
    req = Request(url, headers={"User-Agent": "EV-Router-Jerusalem/1.0"})
    try:
        with urlopen(req, timeout=5) as resp:
            results = _json.loads(resp.read())
    except Exception:
        return jsonify({"error": "geocoding failed"}), 500

    if not results:
        return jsonify({"error": "not found"}), 404

    return jsonify({
        "lat": float(results[0]["lat"]),
        "lon": float(results[0]["lon"]),
        "display_name": results[0].get("display_name", q)
    })


# ──────────────────────────────────────────
# API: הצעות השלמה לכתובת
# ──────────────────────────────────────────
@app.route("/api/suggest")
def api_suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    if "ירושלים" not in q and "jerusalem" not in q.lower():
        q += ", ירושלים"

    url = "https://nominatim.openstreetmap.org/search?q=" + quote(q) + "&format=json&limit=5&countrycodes=il&accept-language=he"
    req = Request(url, headers={"User-Agent": "EV-Router-Jerusalem/1.0"})
    try:
        with urlopen(req, timeout=5) as resp:
            results = _json.loads(resp.read())
        return jsonify([
            {"lat": float(r["lat"]), "lon": float(r["lon"]), "display": r["display_name"]}
            for r in results
        ])
    except Exception:
        return jsonify([])


# ──────────────────────────────────────────
# API: דיווח עומס על תחנה
# ──────────────────────────────────────────
@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json()
    station_id = data.get("station_id")
    count = int(data.get("count", 0))
    if station_id:
        OCCUPANCY[station_id] = max(0, count)
        _save_data()
    return jsonify({"ok": True, "station_id": station_id, "count": OCCUPANCY.get(station_id, 0)})


@app.route("/api/occupancy")
def api_occupancy():
    return jsonify(OCCUPANCY)


# ──────────────────────────────────────────
# API: רישום נהג מהקובץ הstandalone
# ──────────────────────────────────────────
@app.route("/api/log-driver", methods=["POST", "OPTIONS"])
def api_log_driver():
    if request.method == "OPTIONS":
        r = jsonify({"ok": True})
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return r
    data = request.get_json() or {}
    lat = float(data.get("lat", 0))
    lon = float(data.get("lon", 0))
    if not lat or not lon:
        return jsonify({"ok": False}), 400
    _cleanup_drivers()
    _driver_counter[0] += 1
    driver_id = f"S{_driver_counter[0]:03d}"  # S = Standalone
    RECENT_DRIVERS[driver_id] = {
        "id": driver_id, "lat": lat, "lon": lon,
        "ts": time.time(),
        "ts_str": datetime.now().strftime("%H:%M"),
        "assignment": data.get("assignment"),
    }
    _save_data()
    r = jsonify({"ok": True, "driver_id": driver_id})
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    local_ip = _get_local_ip()
    print("=" * 50)
    print("  EV Router Jerusalem")
    print(f"  http://localhost:5000      ← מחשב")
    print(f"  http://{local_ip}:5000  ← פלאפון (אותה רשת WiFi)")
    print("=" * 50)
    app.run(debug=True, port=5000, host="0.0.0.0")
