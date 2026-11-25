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
  pip install -r requirements.txt
  py app.py
  ← פותח דפדפן על http://localhost:5000

הנתונים: כל עמדות הטעינה בארץ (stations_israel.json, מתוך מאגר משרד האנרגיה).
דשבורד המנהל (/admin) מוגן בהתחברות — ראו auth.py.
"""

import csv
import io
import os
import random
import socket
import json as _json
from zoneinfo import ZoneInfo
from urllib.request import urlopen, Request
from urllib.parse import quote
from flask import Flask, Response, render_template, request, jsonify, session
from algorithm import run_algorithm, STATIONS, STATIONS_BY_ID, haversine
import db
import auth
from auth import admin_required

app = Flask(__name__)

# ── התחברות מנהלים ──
auth.init_app(app)

# ── מסד נתונים (Supabase / Neon בענן, SQLite במחשב) ──
db.init_db()

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

def _ts_str(dt):
    """שעה בשעון ישראל לתצוגה (HH:MM)."""
    return dt.astimezone(ISRAEL_TZ).strftime("%H:%M") if dt else ""

def _enrich(assignment):
    """משלים שדות לתצוגה (fast, operator) מתוך נתוני התחנות."""
    if not assignment:
        return None
    s = STATIONS_BY_ID.get(assignment.get("station_id"))
    if s:
        assignment = {**assignment, "fast": s["fast"] > 0, "operator": s["operator"]}
    return assignment

def get_active_drivers():
    """נהגים פעילים = חיפושים מהשעה האחרונה (מתוך המסד)."""
    return [
        {"id": r["driver_id"], "lat": r["lat"], "lon": r["lon"],
         "ts_str": _ts_str(r["created_at"]), "stored_assignment": _enrich(r["assignment"])}
        for r in db.recent_searches(60)
    ]

# ──────────────────────────────────────────
# נהגי demo — 30 נהגים אקראיים סביב מיקום החיפוש
# ──────────────────────────────────────────
def demo_drivers_near(lat, lon, n=30, spread=0.03):
    """
    יוצר n נהגי רקע בסביבת הנהג (±spread מעלות ≈ ±3 ק"מ),
    כדי שבכל מקום בארץ יהיו "מתחרים" על העמדות.
    ה-seed תלוי במיקום (מעוגל), כך שאותו אזור תמיד מקבל אותם נהגים.
    """
    rng = random.Random(f"{round(lat, 2)},{round(lon, 2)}")
    return [
        {"id": f"D{i+1}",
         "lat": round(lat + rng.uniform(-spread, spread), 4),
         "lon": round(lon + rng.uniform(-spread, spread), 4)}
        for i in range(n)
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
# דף מנהל — /admin (רק למנהלים מחוברים)
# ──────────────────────────────────────────
_ADMIN_BAR = """
<div id="admin-session-bar" dir="rtl" style="position:fixed;bottom:14px;left:14px;z-index:99999;
  background:#0f2a22;color:#fff;font:13px 'Segoe UI',Arial,sans-serif;padding:8px 12px;
  border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.3);display:flex;gap:10px;align-items:center">
  <span>מחובר/ת: <b>{name}</b></span>
  <a href="/admin/logout" style="color:#7fe0b4;text-decoration:none;font-weight:700">התנתקות</a>
</div>
"""

@app.route("/admin")
@admin_required
def admin_page():
    html = render_template("admin.html")
    # מוסיפים לדשבורד פס קטן עם שם המנהל וכפתור התנתקות
    from markupsafe import escape
    bar = _ADMIN_BAR.format(name=escape(session.get("admin", "")))
    i = html.rfind("</body>")
    html = html[:i] + bar + html[i:] if i != -1 else html + bar
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ──────────────────────────────────────────
# API: חיפוש עמדה לנהג בודד
# ──────────────────────────────────────────
@app.route("/api/find", methods=["POST"])
def api_find():
    """
    מקבל JSON מהדפדפן: { lat, lon, preference }
    מריץ את האלגוריתם עבור נהג בודד + נהגי הדמו שסביבו
    מחזיר JSON עם הקצאה ורשימת עמדות
    """
    data = request.get_json()
    lat = float(data.get("lat", 31.775))
    lon = float(data.get("lon", 35.213))
    preference = data.get("preference", "any")

    user_driver = {"id": "USER", "lat": lat, "lon": lon}
    all_drivers = [user_driver] + demo_drivers_near(lat, lon)

    # priority_driver="USER": הנהג שמחפש מקבל את התחנה הקרובה הפנויה,
    # ושאר הנהגים משובצים ב-Min-Cost Max-Flow
    result = run_algorithm(all_drivers, preference=preference, max_dist=5.0,
                           occupancy_data=db.get_occupancy(), priority_driver="USER")

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

    # שמירת החיפוש במסד הנתונים (כל חיפוש = שורה חדשה)
    db.save_search(db.next_driver_id("R"), lat, lon, preference, "web", user_assignment)

    return jsonify({
        "assignment": user_assignment,
        "nearby_stations": stations_with_dist,
        "stats": result["stats"],
    })


# ──────────────────────────────────────────
# API: הרצת האלגוריתם לדשבורד מנהל
# ──────────────────────────────────────────
@app.route("/api/run", methods=["POST"])
@admin_required
def api_run():
    """
    מריץ את האלגוריתם על כל הנהגים הפעילים (שעה אחרונה).
    מחזיר תוצאות מלאות לדשבורד המנהל.
    """
    data = request.get_json(silent=True) or {}
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
    result = run_algorithm(algo_drivers, preference=preference, max_dist=5.0, occupancy_data=db.get_occupancy())

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
# API: נתוני תחנות (כל הארץ)
# ──────────────────────────────────────────
@app.route("/api/stations")
def api_stations():
    return jsonify(STATIONS)


# ──────────────────────────────────────────
# API: גיאוקודינג כתובת → קואורדינטות (כל הארץ)
# ──────────────────────────────────────────
@app.route("/api/geocode")
def api_geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "no query"}), 400

    url = "https://nominatim.openstreetmap.org/search?q=" + quote(q) + "&format=json&limit=1&countrycodes=il"
    req = Request(url, headers={"User-Agent": "EV-Router-Israel/1.0"})
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

    url = "https://nominatim.openstreetmap.org/search?q=" + quote(q) + "&format=json&limit=5&countrycodes=il&accept-language=he"
    req = Request(url, headers={"User-Agent": "EV-Router-Israel/1.0"})
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
# API: דיווח עומס על תחנה (פתוח לנהגים)
# ──────────────────────────────────────────
@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json()
    station_id = data.get("station_id")
    count = int(data.get("count", 0))
    if station_id:
        db.report_occupancy(station_id, max(0, count))
    return jsonify({"ok": True, "station_id": station_id, "count": db.get_occupancy().get(station_id, 0)})


@app.route("/api/occupancy")
def api_occupancy():
    return jsonify(db.get_occupancy())


# ──────────────────────────────────────────
# API: היסטוריית כל החיפושים (למנהל בלבד)
# ──────────────────────────────────────────
@app.route("/api/history")
@admin_required
def api_history():
    limit = request.args.get("limit", type=int) or 500
    rows = db.all_searches(limit)
    return jsonify({
        "total": db.count_searches(),
        "searches": [{
            **{k: v for k, v in r.items() if k != "created_at"},
            "created_at": r["created_at"].astimezone(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M"),
        } for r in rows],
    })


@app.route("/api/export.csv")
@admin_required
def api_export_csv():
    """הורדת כל החיפושים כקובץ CSV (נפתח באקסל) — למנהל בלבד."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["מזהה", "נהג", "תאריך ושעה", "מקור", "קו רוחב", "קו אורך", "העדפה",
                "עמדה", "שם עמדה", "עיר", "מרחק (ק\"מ)", "עמדות מלאות שדולגו"])
    for r in db.all_searches():
        a = r["assignment"] or {}
        w.writerow([r["id"], r["driver_id"],
                    r["created_at"].astimezone(ISRAEL_TZ).strftime("%d/%m/%Y %H:%M"),
                    r["source"], r["lat"], r["lon"], r["preference"],
                    a.get("station_id", ""), a.get("station_name", ""), a.get("city", ""),
                    a.get("dist", ""), a.get("skipped_full", "")])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=ev_router_searches.csv"})


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
    # הקובץ העצמאי שולח רק station_id — משלימים את הפרטים מנתוני התחנות
    asg = data.get("assignment")
    if isinstance(asg, str):
        s = STATIONS_BY_ID.get(asg)
        asg = {"station_id": s["id"], "station_name": s["name"], "city": s.get("city", "")} if s else None
        if asg and data.get("dist") is not None:
            asg["dist"] = data.get("dist")
        if asg and data.get("skipped_full") is not None:
            asg["skipped_full"] = data.get("skipped_full")
    driver_id = db.next_driver_id("S")  # S = Standalone
    db.save_search(driver_id, lat, lon, data.get("preference"), "standalone", asg)
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
    n_admins = len(auth.load_admins())
    print("=" * 50)
    print(f"  EV Router Israel — {len(STATIONS)} אתרי טעינה")
    print(f"  מסד נתונים: {db.DB_KIND} · {db.count_searches()} חיפושים שמורים")
    print(f"  מנהלים מוגדרים: {n_admins}" + ("" if n_admins else "  ← צרי admin_users.txt"))
    print(f"  http://localhost:5000      ← מחשב")
    print(f"  http://localhost:5000/admin ← דשבורד מנהל")
    print(f"  http://{local_ip}:5000  ← פלאפון (אותה רשת WiFi)")
    print("=" * 50)
    app.run(debug=True, port=5000, host="0.0.0.0")
