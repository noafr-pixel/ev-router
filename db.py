# -*- coding: utf-8 -*-
"""
db.py — שכבת מסד הנתונים
-------------------------
שומר את כל החיפושים ודיווחי העומס במסד נתונים קבוע.

איפה הנתונים נשמרים:
  - אם מוגדר משתנה סביבה DATABASE_URL (כתובת החיבור של Supabase או Neon) →
    PostgreSQL בענן. זה מה שרץ ב-Render.
    ב-Supabase משתמשים בכתובת "Session pooler" (פורט 5432),
    כי Render לא יכול להתחבר לכתובת הישירה (IPv6).
  - אחרת → קובץ SQLite מקומי בשם ev_router.db ליד app.py.
    כך אפשר להריץ במחשב בלי להגדיר כלום.

טבלאות (ERD):
  searches           — כל חיפוש של נהג (שורה לכל חיפוש)
  occupancy_reports  — היסטוריית כל דיווחי העומס
  station_occupancy  — העומס הנוכחי בכל תחנה (הדיווח האחרון)
"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Column, DateTime, Float, Integer, MetaData, String, Table,
    create_engine, desc, func, insert, select, text, update,
)

# ──────────────────────────────────────────
# חיבור
# ──────────────────────────────────────────
_url = os.environ.get("DATABASE_URL", "").strip()
if _url:
    # Supabase/Neon נותנים כתובת שמתחילה ב-postgres:// או postgresql://
    # SQLAlchemy צריך לדעת להשתמש בדרייבר psycopg (גרסה 3)
    if _url.startswith("postgres://"):
        _url = "postgresql+psycopg://" + _url[len("postgres://"):]
    elif _url.startswith("postgresql://"):
        _url = "postgresql+psycopg://" + _url[len("postgresql://"):]
    engine = create_engine(_url, pool_pre_ping=True, pool_recycle=300)
    DB_KIND = "PostgreSQL (Supabase)" if "supabase" in _url else "PostgreSQL"
else:
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ev_router.db")
    engine = create_engine(f"sqlite:///{_path}")
    DB_KIND = "SQLite (מקומי)"

metadata = MetaData()

searches = Table(
    "searches", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("driver_id", String(20), nullable=False),        # R001 = אתר, S001 = קובץ עצמאי
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("lat", Float, nullable=False),
    Column("lon", Float, nullable=False),
    Column("preference", String(10)),                       # any / fast / slow
    Column("source", String(20)),                           # web / standalone
    Column("station_id", String(20)),                       # NULL = לא נמצאה עמדה
    Column("station_name", String(300)),
    Column("station_city", String(100)),
    Column("dist_km", Float),
    Column("skipped_full", Integer),                        # כמה עמדות קרובות יותר היו מלאות
)

occupancy_reports = Table(
    "occupancy_reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("station_id", String(20), nullable=False, index=True),
    Column("count", Integer, nullable=False),
)

station_occupancy = Table(
    "station_occupancy", metadata,
    Column("station_id", String(20), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def init_db():
    """יוצר את הטבלאות אם הן לא קיימות (בטוח להריץ בכל הפעלה)."""
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # ב-Supabase כל טבלה ב-public חשופה גם דרך ה-API הציבורי של Supabase.
        # Row Level Security בלי שום policy = ה-API לא רואה כלום.
        # השרת שלנו מתחבר כבעלים של הטבלאות, ולכן ממשיך לקרוא ולכתוב כרגיל.
        for t in metadata.sorted_tables:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{t.name}" ENABLE ROW LEVEL SECURITY'))
            except Exception as e:
                print(f"  (RLS לא הופעל על {t.name}: {e.__class__.__name__})")


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    """SQLite מחזיר תאריכים בלי אזור זמן — מניחים UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ──────────────────────────────────────────
# חיפושים
# ──────────────────────────────────────────
def next_driver_id(prefix):
    """מזהה רץ לנהג: R001, R002... (לפי מספר החיפושים שנשמרו)."""
    with engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(searches)).scalar() or 0
    return f"{prefix}{n + 1:03d}"


def save_search(driver_id, lat, lon, preference, source, assignment):
    a = assignment or {}
    with engine.begin() as conn:
        conn.execute(insert(searches).values(
            driver_id=driver_id,
            created_at=_now(),
            lat=lat, lon=lon,
            preference=preference,
            source=source,
            station_id=a.get("station_id"),
            station_name=a.get("station_name"),
            station_city=a.get("city"),
            dist_km=a.get("dist"),
            skipped_full=a.get("skipped_full"),
        ))


def _row_to_search(r):
    return {
        "id": r.id,
        "driver_id": r.driver_id,
        "created_at": _aware(r.created_at),
        "lat": r.lat, "lon": r.lon,
        "preference": r.preference,
        "source": r.source,
        "assignment": ({
            "station_id": r.station_id,
            "station_name": r.station_name,
            "city": r.station_city,
            "dist": r.dist_km,
            "skipped_full": r.skipped_full,
        } if r.station_id else None),
    }


def recent_searches(minutes=60):
    """חיפושים מהשעה האחרונה — אלה ה"נהגים הפעילים" בדשבורד."""
    since = _now() - timedelta(minutes=minutes)
    with engine.connect() as conn:
        rows = conn.execute(
            select(searches).where(searches.c.created_at >= since).order_by(searches.c.id)
        ).all()
    return [_row_to_search(r) for r in rows]


def all_searches(limit=None):
    """כל החיפושים, מהחדש לישן."""
    q = select(searches).order_by(desc(searches.c.id))
    if limit:
        q = q.limit(limit)
    with engine.connect() as conn:
        rows = conn.execute(q).all()
    return [_row_to_search(r) for r in rows]


def count_searches():
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(searches)).scalar() or 0


# ──────────────────────────────────────────
# עומס בתחנות
# ──────────────────────────────────────────
def get_occupancy():
    """{station_id: count} — הדיווח האחרון לכל תחנה."""
    with engine.connect() as conn:
        rows = conn.execute(select(station_occupancy.c.station_id, station_occupancy.c.count)).all()
    return {r.station_id: r.count for r in rows}


def report_occupancy(station_id, count):
    now = _now()
    with engine.begin() as conn:
        conn.execute(insert(occupancy_reports).values(created_at=now, station_id=station_id, count=count))
        res = conn.execute(
            update(station_occupancy)
            .where(station_occupancy.c.station_id == station_id)
            .values(count=count, updated_at=now)
        )
        if res.rowcount == 0:
            conn.execute(insert(station_occupancy).values(station_id=station_id, count=count, updated_at=now))
