# -*- coding: utf-8 -*-
"""
algorithm.py
------------
מכיל:
  - נתוני התחנות (STATIONS) — נטענים מהקובץ stations_israel.json (כל הארץ)
  - פונקציית haversine לחישוב מרחקים
  - פונקציית build_graph לבניית גרף הזרימה (עם עלויות = מרחק)
  - מימוש Min-Cost Max-Flow באמצעות Cost-Scaling Push-Relabel (Goldberg-Tarjan)
  - פונקציית run_algorithm שמחזירה תוצאות מסודרות

מקור הנתונים:
  מאגר עמדות הטעינה של משרד האנרגיה (Cello Charge), נשלף ב-21.11.2026.
  כל רשומה = אתר טעינה:
    cap  = מספר השקעים באתר (כמה רכבים יכולים להיטען בו-זמנית)
    fast = שקעי DC (טעינה מהירה)
    slow = שקעי AC (טעינה איטית)

המודל:
  SOURCE → נהג   : קיבולת 1, עלות 0
  נהג → תחנה     : קיבולת 1, עלות = מרחק במטרים (רק תחנות ברדיוס)
  תחנה → SINK    : קיבולת = שקעים פנויים (cap פחות דיווחי עומס), עלות 0

  מטרה (לפי סדר עדיפות):
    1. הנהג שמחפש עכשיו (priority_driver) מקבל את התחנה הקרובה ביותר
       שיש בה שקע פנוי. אם הקרובה מלאה — את הבאה אחריה, וכן הלאה.
    2. לשאר הנהגים: למקסם את מספר הנהגים ששובצו (Maximum Flow)
    3. מבין כל השיבוצים המקסימליים — למזער את סכום המרחקים (Minimum Cost)
"""

import json
import math
import os
from collections import defaultdict, deque

# ──────────────────────────────────────────
# נתוני התחנות — כל הארץ
# ──────────────────────────────────────────
_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stations_israel.json")
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    STATIONS = json.load(_f)

STATIONS_BY_ID = {s["id"]: s for s in STATIONS}


# ──────────────────────────────────────────
# מרחק Haversine
# ──────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    """
    מחשב מרחק בק"מ בין שתי נקודות גאוגרפיות (מרחק אווירי).
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


def stations_near(lat, lon, max_dist, preference="any"):
    """
    מחזיר [(תחנה, מרחק)] ברדיוס max_dist ק"מ, ממוינות מהקרובה לרחוקה.
    קודם סינון מהיר לפי "ריבוע" של קווי רוחב/אורך, ורק אז haversine —
    כדי לא לחשב מרחק לכל 3,000+ התחנות בארץ.
    """
    dlat = max_dist / 111.0
    dlon = max_dist / (111.0 * math.cos(math.radians(lat)))
    out = []
    for s in STATIONS:
        if abs(s["lat"] - lat) > dlat or abs(s["lon"] - lon) > dlon:
            continue
        if preference == "fast" and s["fast"] == 0:
            continue
        if preference == "slow" and s["slow"] == 0:
            continue
        dist = haversine(lat, lon, s["lat"], s["lon"])
        if dist <= max_dist:
            out.append((s, dist))
    out.sort(key=lambda x: x[1])
    return out


def free_slots(station, occupancy_data):
    """שקעים פנויים בתחנה = קיבולת פחות רכבים שדווחו בה."""
    reported = (occupancy_data or {}).get(station["id"], 0)
    return max(0, station["cap"] - reported)


# ──────────────────────────────────────────
# רשת זרימה עם עלויות
# ──────────────────────────────────────────
class FlowNetwork:
    """
    גרף מכוון עם קיבולת ועלות לכל קשת.
    כל קשת נשמרת יחד עם הקשת ההפוכה שלה (residual) — אינדקס e ו-e^1.
    """

    def __init__(self):
        self.index = {}       # שם צומת → מספר
        self.names = []       # מספר → שם צומת
        self.adj = []         # לכל צומת: רשימת אינדקסים של קשתות יוצאות
        self.to = []
        self.cap = []
        self.cost = []
        self.flow = []

    def node(self, name):
        if name not in self.index:
            self.index[name] = len(self.names)
            self.names.append(name)
            self.adj.append([])
        return self.index[name]

    def add_edge(self, u, v, cap, cost):
        u, v = self.node(u), self.node(v)
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.adj[a].append(len(self.to))
            self.to.append(b)
            self.cap.append(c)
            self.cost.append(w)
            self.flow.append(0)

    def residual(self, e):
        return self.cap[e] - self.flow[e]

    def flow_between(self, u, v):
        if u not in self.index or v not in self.index:
            return 0
        iu, iv = self.index[u], self.index[v]
        return sum(self.flow[e] for e in self.adj[iu] if self.to[e] == iv and self.cap[e] > 0)


# ──────────────────────────────────────────
# Min-Cost Max-Flow: Cost-Scaling Push-Relabel
# ──────────────────────────────────────────
def min_cost_max_flow(net, source, sink, max_flow_bound):
    """
    אלגוריתם Cost-Scaling Push-Relabel (Goldberg & Tarjan, 1990).

    רעיון:
      - מוסיפים קשת SINK → SOURCE עם עלות שלילית גדולה מאוד (−BIG).
        כך כל יחידת זרימה נוספת "משתלמת" יותר מכל מרחק,
        ולכן הפתרון הזול ביותר הוא גם בעל הזרימה המקסימלית.
      - כל צומת מקבל "מחיר" p (במקום ה"גובה" של Push-Relabel הרגיל).
        עלות מופחתת של קשת: c_p(u,v) = cost(u,v) + p(u) − p(v).
      - קשת "קבילה" (admissible) = יש בה קיבולת שארית ו-c_p < 0.
      - Push: דוחפים עודף (excess) לאורך קשת קבילה.
      - Relabel: אם אין קשת קבילה — מורידים את המחיר של הצומת
        כך שתיווצר קשת עם c_p = −ε.
      - ε מתחיל גדול ומוקטן פי α בכל שלב (scaling).
        כשהעלויות מוכפלות ב-n ו-ε מגיע ל-1, הפתרון אופטימלי.

    מחזיר: (כמות זרימה, עלות כוללת, מספר פעולות)
    """
    s, t = net.index[source], net.index[sink]
    n = len(net.names)

    # BIG גדול מכל עלות אפשרית של מסלול שיפור → זרימה מקסימלית קודמת למרחק
    max_edge_cost = max([c for c in net.cost if c > 0], default=1)
    BIG = max_edge_cost * n + 1
    net.add_edge(sink, source, max_flow_bound, -BIG)
    ret_edge = len(net.to) - 2

    m = len(net.to)
    scaled = [c * n for c in net.cost]                 # עלויות מוכפלות ב-n
    price = [0] * n
    excess = [0] * n
    alpha = 8
    eps = max(abs(c) for c in scaled) or 1
    ops = 0

    def reduced(e, u):
        return scaled[e] + price[u] - price[net.to[e]]

    while True:
        eps = max(1, eps // alpha)

        # ── refine(ε) ──
        # 1. רוויה של כל קשת עם עלות מופחתת שלילית
        for u in range(n):
            for e in net.adj[u]:
                r = net.residual(e)
                if r > 0 and reduced(e, u) < 0:
                    v = net.to[e]
                    net.flow[e] += r
                    net.flow[e ^ 1] -= r
                    excess[u] -= r
                    excess[v] += r

        # 2. Push / Relabel עד שאין צמתים פעילים
        active = deque(u for u in range(n) if excess[u] > 0)
        in_q = [excess[u] > 0 for u in range(n)]
        cur = [0] * n
        while active:
            u = active.popleft()
            in_q[u] = False
            while excess[u] > 0:
                if cur[u] == len(net.adj[u]):
                    # Relabel: מחיר חדש כך שתיווצר קשת קבילה עם c_p = −ε
                    best = None
                    for e in net.adj[u]:
                        if net.residual(e) > 0:
                            val = price[net.to[e]] - scaled[e]
                            if best is None or val > best:
                                best = val
                    price[u] = best - eps
                    cur[u] = 0
                    ops += 1
                    continue
                e = net.adj[u][cur[u]]
                r = net.residual(e)
                if r > 0 and reduced(e, u) < 0:
                    # Push
                    v = net.to[e]
                    d = min(excess[u], r)
                    net.flow[e] += d
                    net.flow[e ^ 1] -= d
                    excess[u] -= d
                    excess[v] += d
                    ops += 1
                    if excess[v] > 0 and not in_q[v]:
                        active.append(v)
                        in_q[v] = True
                else:
                    cur[u] += 1

        if eps == 1:
            break

    total_flow = net.flow[ret_edge]
    total_cost = sum(net.flow[e] * net.cost[e] for e in range(0, m, 2) if e != ret_edge)
    return total_flow, total_cost, ops


# ──────────────────────────────────────────
# בניית גרף הזרימה
# ──────────────────────────────────────────
def build_graph(drivers, preference="any", max_dist=5.0, occupancy_data=None, reserved=None):
    """
    בונה את רשת הזרימה:
      SOURCE → נהגים → תחנות → SINK
    עלות קשת נהג→תחנה = מרחק במטרים.

    רק תחנות שבטווח של לפחות נהג אחד נכנסות לגרף,
    כך שהגרף נשאר קטן גם כשהמאגר כולל את כל הארץ.

    reserved: {station_id: count} — שקעים שכבר הוקצו (לנהג בעדיפות),
              ולכן לא זמינים לשאר הנהגים.
    """
    SOURCE, SINK = "SOURCE", "SINK"
    net = FlowNetwork()
    net.node(SOURCE)
    net.node(SINK)

    driver_reachable = {}
    used = {}

    for d in drivers:
        net.add_edge(SOURCE, d["id"], 1, 0)
        reach = []
        for s, dist in stations_near(d["lat"], d["lon"], max_dist, preference):
            net.add_edge(d["id"], s["id"], 1, int(round(dist * 1000)))
            used[s["id"]] = s
            reach.append({"station_id": s["id"], "dist": round(dist, 2)})
        driver_reachable[d["id"]] = reach

    for sid, s in used.items():
        avail = free_slots(s, occupancy_data) - (reserved or {}).get(sid, 0)
        if avail > 0:
            net.add_edge(sid, SINK, avail, 0)

    return net, driver_reachable, list(used.values()), SOURCE, SINK


# ──────────────────────────────────────────
# שלב 1 — עדיפות לנהג המחפש: התחנה הקרובה הפנויה
# ──────────────────────────────────────────
def nearest_available(driver, preference, max_dist, occupancy_data):
    """
    עובר על התחנות מהקרובה לרחוקה ומחזיר את הראשונה שיש בה שקע פנוי.
    מחזיר גם כמה תחנות קרובות יותר דולגו כי היו מלאות.
    """
    skipped_full = 0
    for s, dist in stations_near(driver["lat"], driver["lon"], max_dist, preference):
        if free_slots(s, occupancy_data) > 0:
            return s, dist, skipped_full
        skipped_full += 1
    return None, None, skipped_full


# ──────────────────────────────────────────
# פונקציה ראשית — זו שה-app.py קורא לה
# ──────────────────────────────────────────
def run_algorithm(drivers, preference="any", max_dist=5.0, occupancy_data=None, priority_driver=None):
    """
    מקבל:
      drivers         - רשימת נהגים [{"id":"D1","lat":...,"lon":...}, ...]
      preference      - "any" / "fast" / "slow"
      max_dist        - רדיוס בק"מ
      occupancy_data  - {station_id: count} דיווחי עומס אמיתיים
      priority_driver - id של הנהג שמחפש עכשיו (לדוגמה "USER"), או None

    מחזיר dict עם כל התוצאות לממשק:
      total_flow, assignments, station_loads, unassigned, stats
    """
    import time
    t0 = time.time()

    assignments = {}
    reserved = {}

    # ── שלב 1: הנהג המחפש מקבל את הקרובה הפנויה ──
    others = drivers
    if priority_driver is not None:
        pd = next((d for d in drivers if d["id"] == priority_driver), None)
        if pd is not None:
            others = [d for d in drivers if d["id"] != priority_driver]
            s, dist, skipped = nearest_available(pd, preference, max_dist, occupancy_data)
            if s is not None:
                reserved[s["id"]] = 1
                assignments[pd["id"]] = {
                    "station_id": s["id"],
                    "station_name": s["name"],
                    "city": s.get("city", ""),
                    "dist": round(dist, 2),
                    "fast": s["fast"] > 0,
                    "operator": s["operator"],
                    "skipped_full": skipped,
                }

    # ── שלב 2: Min-Cost Max-Flow לשאר הנהגים ──
    net, driver_reachable, used, SOURCE, SINK = build_graph(
        others, preference, max_dist, occupancy_data, reserved
    )
    total_flow, total_cost, ops = min_cost_max_flow(net, SOURCE, SINK, max(len(others), 1))

    for d in others:
        for r in driver_reachable.get(d["id"], []):
            if net.flow_between(d["id"], r["station_id"]) > 0:
                s = STATIONS_BY_ID[r["station_id"]]
                assignments[d["id"]] = {
                    "station_id": s["id"],
                    "station_name": s["name"],
                    "city": s.get("city", ""),
                    "dist": r["dist"],
                    "fast": s["fast"] > 0,
                    "operator": s["operator"],
                }
                break

    # תחנות להצגה: כל מה שבגרף + התחנות סביב הנהג בעדיפות
    shown = {s["id"]: s for s in used}
    if priority_driver is not None:
        pd = next((d for d in drivers if d["id"] == priority_driver), None)
        if pd is not None:
            for s, _ in stations_near(pd["lat"], pd["lon"], max_dist, preference):
                shown[s["id"]] = s
            driver_reachable[pd["id"]] = [
                {"station_id": s["id"], "dist": round(dist, 2)}
                for s, dist in stations_near(pd["lat"], pd["lon"], max_dist, preference)
            ]

    elapsed = round(time.time() - t0, 4)

    # עומס לכל תחנה — לפי דיווחי משתמשים בלבד (ללא סימולציה)
    station_loads = {}
    for s in shown.values():
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

    drivers_out = []
    for d in drivers:
        reach = driver_reachable.get(d["id"], [])
        drivers_out.append({
            **d,
            "has_fast": any(STATIONS_BY_ID[r["station_id"]]["fast"] > 0 for r in reach),
            "reachable_count": len(reach),
            "assignment": assignments.get(d["id"]),
        })

    n_loads = max(len(station_loads), 1)
    total_km = round(sum(a["dist"] for a in assignments.values()), 2)
    return {
        "total_flow": len(assignments),
        "drivers": drivers_out,
        "station_loads": list(station_loads.values()),
        "assignments": assignments,
        "unassigned": unassigned,
        "stats": {
            "n_drivers": len(drivers),
            "n_assigned": len(assignments),
            "n_unassigned": len(unassigned),
            "n_stations": len(STATIONS),
            "n_stations_in_range": len(station_loads),
            "n_full": sum(1 for s in station_loads.values() if s["fill_pct"] >= 100),
            "avg_load": round(sum(s["fill_pct"] for s in station_loads.values()) / n_loads, 1),
            "total_km": total_km,
            "avg_km": round(total_km / max(len(assignments), 1), 2),
            "elapsed": elapsed,
            "iterations": ops,
        }
    }
