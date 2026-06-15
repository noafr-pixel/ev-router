# -*- coding: utf-8 -*-
"""
algorithm.py
------------
מכיל:
  - נתוני התחנות (STATIONS) מוגדרים ישירות (ללא Excel)
  - פונקציית haversine לחישוב מרחקים
  - פונקציית build_graph לבניית גרף הזרימה
  - מימוש Push-Relabel
  - פונקציית run_algorithm שמחזירה תוצאות מסודרות

הסבר קצר על Flask + Python:
  כל פונקציה בקובץ זה היא "כלי" שה-app.py (השרת) משתמש בו.
  app.py מייבא מכאן ומפעיל run_algorithm() כשהמשתמש לוחץ "חפש".
"""

import math
from collections import defaultdict

# ──────────────────────────────────────────
# נתוני התחנות — מבוססים על ה-Excel שלכם
# ──────────────────────────────────────────
STATIONS = [
    {"id": "S638",  "name": "יקותיאל אדם 2 ירושלים",          "operator": "AdviceCPW",    "lat": 31.7703, "lon": 35.2145, "cap": 4,  "fast": 0,  "slow": 4},
    {"id": "S462",  "name": "דרך שלווה ירושלים",               "operator": "LishatechCPW", "lat": 31.7621, "lon": 35.2089, "cap": 10, "fast": 6,  "slow": 4},
    {"id": "S1981", "name": "שדרות ירושלים 1",                  "operator": "Greenspot",    "lat": 31.8040, "lon": 35.2165, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S565",  "name": "קניון רב שפע שמגר ירושלים",        "operator": "AdviceCPW",    "lat": 31.7954, "lon": 35.2145, "cap": 4,  "fast": 4,  "slow": 0},
    {"id": "S429",  "name": "HolyLand",                         "operator": "EdgeControl",  "lat": 31.7740, "lon": 35.2140, "cap": 1,  "fast": 1,  "slow": 0},
    {"id": "S454",  "name": "מלונות דן-דן בוטיק ירושלים",       "operator": "AfconEv",      "lat": 31.7499, "lon": 35.2133, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S460",  "name": "חניון תיאטרון ירושלים",            "operator": "AfconEv",      "lat": 31.7725, "lon": 35.2133, "cap": 4,  "fast": 0,  "slow": 4},
    {"id": "S464",  "name": "עסקי - תיאטרון ירושלים",           "operator": "Ev4u",         "lat": 31.7725, "lon": 35.2133, "cap": 3,  "fast": 0,  "slow": 3},
    {"id": "S469",  "name": "מלון ענבל ירושלים",                "operator": "Enova",        "lat": 31.7797, "lon": 35.2249, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S472",  "name": "Gav Yam Jerusalem",                "operator": "InterEv",      "lat": 31.7663, "lon": 35.2057, "cap": 14, "fast": 0,  "slow": 14},
    {"id": "S477",  "name": "מלונות דן - פנורמה ירושלים",       "operator": "AfconEv",      "lat": 31.7746, "lon": 35.2178, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S481",  "name": "מלונות דן - מלון קינג דיויד",      "operator": "AfconEv",      "lat": 31.7769, "lon": 35.2216, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S487",  "name": "ירושלים מלון שלוש הקשתות",         "operator": "Enova",        "lat": 31.7769, "lon": 35.2216, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S502",  "name": "לאונרדו פלאזה ירושלים - פתאל",     "operator": "ViMore",       "lat": 31.7677, "lon": 35.2134, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S513",  "name": "מגדלי ירושלים",                    "operator": "AfconEv",      "lat": 31.7703, "lon": 35.2152, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S523",  "name": "חניון הלאום ירושלים",              "operator": "Enova",        "lat": 31.7752, "lon": 35.2120, "cap": 7,  "fast": 3,  "slow": 4},
    {"id": "S526",  "name": "לאונרדו בוטיק ירושלים - פתאל",    "operator": "ViMore",       "lat": 31.7809, "lon": 35.2234, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S533",  "name": "חניון אגריפס 88 ירושלים",          "operator": "ZenEvCEP",     "lat": 31.7852, "lon": 35.2181, "cap": 24, "fast": 4,  "slow": 20},
    {"id": "S540",  "name": "מגדל הנביאים ירושלים",             "operator": "AfconEv",      "lat": 31.7887, "lon": 35.2070, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S541",  "name": "מלון קיסר ירושלים",                "operator": "Enova",        "lat": 31.7887, "lon": 35.2070, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S542",  "name": "לאונרדו ירושלים - פתאל",           "operator": "ViMore",       "lat": 31.7850, "lon": 35.2284, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S544",  "name": "מלון ירושלים",                     "operator": "Lishatech",    "lat": 31.7850, "lon": 35.2284, "cap": 4,  "fast": 0,  "slow": 4},
    {"id": "S610",  "name": "ציבורי - קדמא נווה אילן ירושלים",  "operator": "Ev4u",         "lat": 31.8030, "lon": 35.1640, "cap": 2,  "fast": 0,  "slow": 2},
    {"id": "S613",  "name": "ציבורי - סי הוטל נווה אילן",       "operator": "Ev4u",         "lat": 31.8030, "lon": 35.1640, "cap": 6,  "fast": 0,  "slow": 6},
    {"id": "S408",  "name": "בית הלוחם ירושלים",                "operator": "Enova",        "lat": 31.7746, "lon": 35.2162, "cap": 4,  "fast": 0,  "slow": 4},
    {"id": "S416",  "name": "קניון עזריאלי ירושלים",            "operator": "SonolEvi",     "lat": 31.7445, "lon": 35.2009, "cap": 9,  "fast": 0,  "slow": 9},
    {"id": "S421",  "name": "אולם תצוגה לובינסקי ירושלים",      "operator": "Gnrgy",        "lat": 31.7690, "lon": 35.2136, "cap": 2,  "fast": 2,  "slow": 0},
]

# ──────────────────────────────────────────
# מרחק Haversine
# ──────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    """
    מחשב מרחק בק"מ בין שתי נקודות גאוגרפיות.
    מתחשב בעקמומיות כדור הארץ.
    """
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ──────────────────────────────────────────
# בניית גרף הזרימה
# ──────────────────────────────────────────
def build_graph(drivers, preference="any", max_dist=5.0, occupancy_data=None):
    """
    בונה את מבנה הנתונים של הגרף:
      SOURCE → נהגים → תחנות → SINK

    preference: "any" / "fast" / "slow"
    max_dist: רדיוס חיפוש בק"מ
    occupancy_data: {station_id: count} — רכבים מדווחים כרגע בתחנה
    """
    SOURCE = "SOURCE"
    SINK = "SINK"

    capacity = defaultdict(lambda: defaultdict(int))
    graph = defaultdict(set)

    def add_edge(u, v, cap):
        capacity[u][v] += cap
        graph[u].add(v)
        graph[v].add(u)

    # SOURCE → כל נהג: קיבולת 1 (נהג אחד = רכב אחד)
    for d in drivers:
        add_edge(SOURCE, d["id"], 1)

    # נהג → תחנות בטווח (עם pre-processing לפי העדפה)
    driver_has_fast = {}
    driver_reachable = {}  # תחנות שכל נהג יכול להגיע אליהן

    for d in drivers:
        has_fast = False
        reachable = []
        for s in STATIONS:
            # סינון לפי העדפה (pre-processing)
            if preference == "fast" and s["fast"] == 0:
                continue
            if preference == "slow" and s["slow"] == 0:
                continue

            dist = haversine(d["lat"], d["lon"], s["lat"], s["lon"])
            if dist <= max_dist:
                add_edge(d["id"], s["id"], 1)
                reachable.append({"station_id": s["id"], "dist": round(dist, 2)})
                if s["fast"] > 0:
                    has_fast = True

        driver_has_fast[d["id"]] = has_fast
        driver_reachable[d["id"]] = reachable

    # תחנה → SINK: קיבולת = עמדות פנויות (בניכוי דיווחי עומס)
    for s in STATIONS:
        reported = (occupancy_data or {}).get(s["id"], 0)
        available = max(0, s["cap"] - reported)
        if available > 0:
            add_edge(s["id"], SINK, available)

    return graph, capacity, driver_has_fast, driver_reachable, SOURCE, SINK


# ──────────────────────────────────────────
# אלגוריתם Push-Relabel
# ──────────────────────────────────────────
def push_relabel(graph, capacity, source, sink, stations_list, drivers, driver_has_fast):
    """
    מימוש Push-Relabel עם עדיפות לתחנות מהירות.

    רעיון:
    - כל צומת מקבל "גובה" (תווית)
    - זרימה נדחפת רק ממקום גבוה לנמוך ממנו ב-1
    - תחנות מהירות מקבלות גובה 1, איטיות 0
    - נהג עם מהיר בטווח מקבל גובה 2 → ידחף למהיר קודם
    """
    nodes = set(graph.keys())
    nodes.update([source, sink])

    height = defaultdict(int)
    excess = defaultdict(int)
    flow = defaultdict(lambda: defaultdict(int))

    height[source] = len(nodes)

    # גבהים לתחנות לפי סוג
    station_ids = {s["id"] for s in stations_list}
    for sid in station_ids:
        s = next((x for x in stations_list if x["id"] == sid), None)
        height[sid] = 1 if s and s["fast"] > 0 else 0

    # דחיפה ראשונית SOURCE → נהגים
    for v in list(graph[source]):
        cap = capacity[source][v]
        if cap > 0:
            flow[source][v] = cap
            flow[v][source] -= cap
            excess[v] += cap
            excess[source] -= cap

    # גבהים לנהגים
    for d in drivers:
        height[d["id"]] = 2 if driver_has_fast.get(d["id"], False) else 1

    def push(u, v):
        delta = min(excess[u], capacity[u][v] - flow[u][v])
        if delta > 0 and height[u] == height[v] + 1:
            flow[u][v] += delta
            flow[v][u] -= delta
            excess[u] -= delta
            excess[v] += delta
            return True
        return False

    def relabel(u):
        min_h = float('inf')
        for v in graph[u]:
            if capacity[u][v] - flow[u][v] > 0:
                min_h = min(min_h, height[v])
        if min_h < float('inf'):
            height[u] = min_h + 1

    active = [v for v in nodes if v != source and v != sink and excess[v] > 0]

    iters = 0
    while active:
        u = active[0]
        pushed = False
        for v in list(graph[u]):
            if excess[u] <= 0:
                break
            if push(u, v):
                pushed = True
                if v != source and v != sink and excess[v] > 0 and v not in active:
                    active.append(v)
        if not pushed:
            relabel(u)
        if excess[u] <= 0:
            active.pop(0)
        iters += 1
        if iters > 100_000:
            break

    total_flow = sum(flow[source][v] for v in graph[source])
    return total_flow, flow, iters


# ──────────────────────────────────────────
# פונקציה ראשית — זו שה-app.py קורא לה
# ──────────────────────────────────────────
def run_algorithm(drivers, preference="any", max_dist=5.0, occupancy_data=None):
    """
    מקבל:
      drivers        - רשימת נהגים [{"id":"D1","lat":...,"lon":...}, ...]
      preference     - "any" / "fast" / "slow"
      max_dist       - רדיוס בק"מ
      occupancy_data - {station_id: count} דיווחי עומס אמיתיים

    מחזיר dict עם כל התוצאות לממשק:
      total_flow, assignments, station_loads, unassigned, stats
    """
    import time
    t0 = time.time()

    graph, capacity, driver_has_fast, driver_reachable, SOURCE, SINK = build_graph(
        drivers, preference, max_dist, occupancy_data
    )

    total_flow, flow, iters = push_relabel(
        graph, capacity, SOURCE, SINK, STATIONS, drivers, driver_has_fast
    )

    elapsed = round(time.time() - t0, 4)

    # חישוב הקצאות
    assignments = {}
    for d in drivers:
        for s in STATIONS:
            if flow[d["id"]][s["id"]] > 0:
                dist = haversine(d["lat"], d["lon"], s["lat"], s["lon"])
                assignments[d["id"]] = {
                    "station_id": s["id"],
                    "station_name": s["name"],
                    "dist": round(dist, 2),
                    "fast": s["fast"] > 0,
                    "operator": s["operator"],
                }
                break

    # עומס לכל תחנה — לפי דיווחי משתמשים בלבד (ללא סימולציה)
    station_loads = {}
    for s in STATIONS:
        reported = (occupancy_data or {}).get(s["id"], None)
        has_report = reported is not None
        fill_pct = min(round(reported / max(s["cap"], 1) * 100), 100) if has_report else 0
        station_loads[s["id"]] = {
            **s,
            "load": reported if has_report else 0,
            "fill_pct": fill_pct,
            "has_report": has_report,
            "status": "full" if fill_pct >= 100 else "busy" if fill_pct >= 50 else "free" if fill_pct > 0 else "empty",
        }

    unassigned = [d["id"] for d in drivers if d["id"] not in assignments]

    # נהגים מועשרים
    drivers_out = []
    for d in drivers:
        asgn = assignments.get(d["id"])
        drivers_out.append({
            **d,
            "has_fast": driver_has_fast.get(d["id"], False),
            "reachable_count": len(driver_reachable.get(d["id"], [])),
            "assignment": asgn,
        })

    return {
        "total_flow": total_flow,
        "drivers": drivers_out,
        "station_loads": list(station_loads.values()),
        "assignments": assignments,
        "unassigned": unassigned,
        "stats": {
            "n_drivers": len(drivers),
            "n_assigned": total_flow,
            "n_unassigned": len(unassigned),
            "n_stations": len(STATIONS),
            "n_full": sum(1 for s in station_loads.values() if s["fill_pct"] >= 100),
            "avg_load": round(sum(s["fill_pct"] for s in station_loads.values()) / len(STATIONS), 1),
            "elapsed": elapsed,
            "iterations": iters,
        }
    }
