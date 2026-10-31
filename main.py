"""Wat vliegt er boven me? Kleine Flask-app: geeft de vliegtuigen terug die
op dit moment (min of meer) recht boven een gegeven positie vliegen.

Databronnen (gratis, geen key):
- posities: opendata.adsb.fi, met api.adsb.lol als reserve
- route + maatschappij + toestel: api.adsbdb.com
De bronnen zelf sturen geen CORS-headers, daarom loopt alles via deze server.
"""

from __future__ import annotations

import csv
import math
import os
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, abort, jsonify, request, send_from_directory

STATIC_DIR = Path(__file__).parent / "static"
AIRPORTS_FILE = Path(__file__).parent / "airports.csv"

# Zoekstraal rond de gebruiker, in zeemijlen (wat de ADS-B API's verwachten).
SEARCH_RADIUS_NM = 20
# Een toestel telt als "boven je" als het minstens zo hoog boven de horizon
# staat. 90 = loodrecht boven je. Bij 35 graden zit een vliegtuig op 10 km
# hoogte binnen ~14 km afstand, een heli op 300 m binnen ~430 m.
MIN_ELEVATION_DEG = 35
MAX_RESULTS = 3
# Ook tonen wat net over je heen kwam of zo komt, zodat je een laagvlieger
# niet mist omdat de app nog moest opstarten. Teruggerekend met koers,
# snelheid en stijg/daalsnelheid; rechte lijn, dus niet te ver terug.
LOOK_BACK_S = 120
LOOK_AHEAD_S = 30
# Kwam hij op zijn dichtste punt minstens zo hoog, dan "kwam over", anders "vloog langs".
OVERHEAD_DEG = 60
# Posities worden doorgerekend naar "nu" (koers + snelheid), maar niet verder
# dan dit: een oud signaal doorrekenen geeft schijnprecisie.
MAX_EXTRAPOLATE_S = 20

POSITION_SOURCES = [
    "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}",
    "https://api.adsb.lol/v2/point/{lat}/{lon}/{dist}",
]
ADSBDB = "https://api.adsbdb.com/v0"
HTTP_TIMEOUT = 8
USER_AGENT = "flighttracker-simple/1.0"

app = Flask(__name__, static_folder=None)
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


class _TTLCache:
    def __init__(self) -> None:
        self._data: dict = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
            return None

    def set(self, key, value, ttl: float) -> None:
        with self._lock:
            if len(self._data) > 5000:
                self._data.clear()
            self._data[key] = (time.time() + ttl, value)


_cache = _TTLCache()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


_COMPASS = ["noord", "noordoost", "oost", "zuidoost", "zuid", "zuidwest", "west", "noordwest"]


def _compass(deg: float | None) -> str | None:
    if deg is None:
        return None
    return _COMPASS[int((deg + 22.5) // 45) % 8]


def _fetch_positions(lat: float, lon: float) -> tuple[float, list[dict]]:
    # Afronden op ~100 m zodat herhaalde verzoeken vanaf dezelfde plek de
    # cache raken en we de gratis API's niet onnodig belasten.
    key = ("pos", round(lat, 3), round(lon, 3))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    last_error = None
    for template in POSITION_SOURCES:
        url = template.format(lat=f"{lat:.4f}", lon=f"{lon:.4f}", dist=SEARCH_RADIUS_NM)
        try:
            resp = _session.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            aircraft = resp.json().get("ac") or []
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            continue
        result = (time.time(), aircraft)
        _cache.set(key, result, ttl=2)
        return result
    raise RuntimeError(f"Geen positiedata beschikbaar: {last_error}")


def _extrapolate(ac: dict, fetched_at: float) -> dict:
    # Positie is `seen_pos` seconden oud op het moment van ophalen. Een
    # vliegtuig op 300 km/u legt 80 m/s af, dus een paar seconden telt al.
    ac = dict(ac)  # cache-object niet aanpassen
    gs, track, seen = ac.get("gs"), ac.get("track"), ac.get("seen_pos")
    if not all(isinstance(v, (int, float)) for v in (gs, track, seen)):
        return ac
    age = min(seen + (time.time() - fetched_at), MAX_EXTRAPOLATE_S)
    dist = gs * 0.514444 * age
    ac["lat"] += dist * math.cos(math.radians(track)) / 111320
    ac["lon"] += dist * math.sin(math.radians(track)) / (111320 * math.cos(math.radians(ac["lat"])))
    return ac


def _pass_info(ac: dict, alt_ft: float, lat: float, lon: float) -> tuple[int, int]:
    """(hoogste elevatie in graden, seconden tot dat moment) binnen het
    venster [-LOOK_BACK_S, +LOOK_AHEAD_S]. Negatief = in het verleden."""
    # Platte benadering rond de gebruiker: ruim nauwkeurig genoeg op ~30 km.
    x = (ac["lon"] - lon) * 111320 * math.cos(math.radians(lat))
    y = (ac["lat"] - lat) * 111320
    gs, track = ac.get("gs"), ac.get("track")
    if isinstance(gs, (int, float)) and isinstance(track, (int, float)):
        v = gs * 0.514444
        vx, vy = v * math.sin(math.radians(track)), v * math.cos(math.radians(track))
    else:
        vx = vy = 0.0
    rate = ac.get("baro_rate", ac.get("geom_rate"))
    vz = rate * 0.3048 / 60 if isinstance(rate, (int, float)) else 0.0
    best = (-90.0, 0)
    for t in range(-LOOK_BACK_S, LOOK_AHEAD_S + 1, 2):
        h = max(alt_ft * 0.3048 + vz * t, 0.0)
        d = max(math.hypot(x + vx * t, y + vy * t), 1.0)
        e = math.degrees(math.atan2(h, d))
        if e > best[0]:
            best = (e, t)
    return round(best[0]), best[1]


def _adsbdb(path: str, ttl: float) -> dict | None:
    key = ("adsbdb", path)
    cached = _cache.get(key)
    if cached is not None:
        return cached or None
    try:
        resp = _session.get(f"{ADSBDB}/{path}", timeout=HTTP_TIMEOUT)
        data = resp.json().get("response") if resp.status_code in (200, 404) else None
    except (requests.RequestException, ValueError):
        return None  # niet cachen, volgende keer opnieuw proberen
    result = data if isinstance(data, dict) else {}
    _cache.set(key, result, ttl=ttl)
    return result or None


def _airport(a: dict | None) -> dict | None:
    if not a:
        return None
    return {
        "city": a.get("municipality") or a.get("name"),
        "name": a.get("name"),
        "code": a.get("iata_code") or a.get("icao_code"),
        "country": a.get("country_name"),
        "lat": a.get("latitude"),
        "lon": a.get("longitude"),
    }


def _load_airports() -> list[dict]:
    # Luchthavens met lijnvluchten, uit OurAirports (public domain); zie README.
    with AIRPORTS_FILE.open(encoding="utf-8") as f:
        return [
            {"city": r["city"] or r["name"], "name": r["name"], "code": r["iata"],
             "country": r["country"], "lat": float(r["lat"]), "lon": float(r["lon"])}
            for r in csv.DictReader(f)
        ]


_AIRPORTS = _load_airports()


def _nearest_airport(lat: float, lon: float, max_m: float) -> dict | None:
    best = min(_AIRPORTS, key=lambda a: _haversine_m(lat, lon, a["lat"], a["lon"]))
    return dict(best) if _haversine_m(lat, lon, best["lat"], best["lon"]) <= max_m else None


def _local_airport(ac: dict, alt_ft: float | None, vertical: str) -> tuple[str, dict] | None:
    # Laag en stijgend vlak bij een luchthaven = net daar vertrokken; laag en
    # dalend = gaat daar landen. Dat weten we zeker uit de positie zelf, en
    # het is betrouwbaarder dan de routedatabase (die zat er bv. bij een
    # Ryanair uit Eindhoven naast met "Dortmund -> Malaga").
    if alt_ft is None:
        return None
    alt_m = alt_ft * 0.3048
    if vertical == "stijgt" and alt_m < 3000:
        ap = _nearest_airport(ac["lat"], ac["lon"], 40_000)
        return ("from", ap) if ap else None
    if vertical == "daalt" and alt_m < 1500:
        ap = _nearest_airport(ac["lat"], ac["lon"], 25_000)
        return ("to", ap) if ap else None
    return None


def _route_is_plausible(route: dict, lat: float, lon: float) -> bool:
    # De routedatabase kent per vluchtnummer één vaste route; soms klopt die
    # niet (meerdere legs, gewijzigd schema). Ligt het toestel ver naast de
    # lijn vertrek-bestemming, dan tonen we liever niks dan iets fouts.
    o, d = route["from"], route["to"]
    if None in (o["lat"], o["lon"], d["lat"], d["lon"]):
        return True
    direct = _haversine_m(o["lat"], o["lon"], d["lat"], d["lon"])
    via = _haversine_m(o["lat"], o["lon"], lat, lon) + _haversine_m(lat, lon, d["lat"], d["lon"])
    return direct < 1000 or via / direct < 1.3


def _route_for(callsign: str | None, lat: float, lon: float) -> dict | None:
    if not callsign:
        return None
    data = _adsbdb(f"callsign/{callsign}", ttl=6 * 3600)
    fr = (data or {}).get("flightroute")
    if not fr:
        return None
    route = {
        "airline": (fr.get("airline") or {}).get("name"),
        "flight_number": fr.get("callsign_iata") or fr.get("callsign"),
        "from": _airport(fr.get("origin")),
        "to": _airport(fr.get("destination")),
    }
    if not route["from"] or not route["to"] or not _route_is_plausible(route, lat, lon):
        # Maatschappij klopt wel (zit in de callsign), alleen de route niet.
        return {"airline": route["airline"], "flight_number": route["flight_number"], "from": None, "to": None}
    return route


def _aircraft_info(hex_id: str) -> dict:
    data = _adsbdb(f"aircraft/{hex_id}", ttl=24 * 3600)
    ac = (data or {}).get("aircraft") or {}
    return {
        "manufacturer": ac.get("manufacturer"),
        "type": ac.get("type"),
        "owner": ac.get("registered_owner"),
    }


def _altitude_ft(ac: dict) -> float | None:
    alt = ac.get("alt_baro")
    if alt == "ground":
        return None
    if isinstance(alt, (int, float)):
        return float(alt)
    geom = ac.get("alt_geom")
    return float(geom) if isinstance(geom, (int, float)) else None


def _vertical(ac: dict) -> str:
    rate = ac.get("baro_rate", ac.get("geom_rate"))
    if not isinstance(rate, (int, float)):
        return "onbekend"
    if rate > 300:
        return "stijgt"
    if rate < -300:
        return "daalt"
    return "vlak"


def _tidy_type(desc: str | None) -> str | None:
    # adsb.fi levert bv. "BOEING 737-900"; leesbaarder als "Boeing 737-900".
    if not desc:
        return None
    return " ".join(w.capitalize() if w.isalpha() else w for w in desc.split())


def _describe(ac: dict, lat: float, lon: float, with_details: bool) -> dict:
    alt_ft = _altitude_ft(ac)
    dist_m = _haversine_m(lat, lon, ac["lat"], ac["lon"])
    callsign = (ac.get("flight") or "").strip() or None
    is_heli = ac.get("category") == "A7" or "HELI" in (ac.get("desc") or "").upper()
    item = {
        "id": ac.get("hex"),
        "callsign": callsign,
        "registration": ac.get("r"),
        "aircraft_type": _tidy_type(ac.get("desc")) or ac.get("t"),
        "is_helicopter": is_heli,
        "altitude_m": round(alt_ft * 0.3048) if alt_ft is not None else None,
        "speed_kmh": round(ac["gs"] * 1.852) if isinstance(ac.get("gs"), (int, float)) else None,
        "vertical": _vertical(ac),
        "distance_m": round(dist_m),
        "direction": _compass(_bearing_deg(lat, lon, ac["lat"], ac["lon"])) if dist_m > 300 else None,
        "heading": _compass(ac.get("track") if isinstance(ac.get("track"), (int, float)) else None),
        "elevation_deg": ac["_elev"],
        # Alleen gezet als hij nu niet boven je is maar net wel was / zo komt.
        "pass": None if not ac.get("_passing") else {
            "in_s": ac["_peak_t"],
            "overhead": ac["_peak_elev"] >= OVERHEAD_DEG,
        },
        "airline": None,
        "flight_number": None,
        "from": None,
        "to": None,
    }
    if not with_details:
        return item
    route = _route_for(callsign, ac["lat"], ac["lon"]) if not is_heli else None
    if route:
        item.update(route)
    local = _local_airport(ac, alt_ft, item["vertical"]) if not is_heli else None
    if local:
        side, ap = local
        if not (item[side] and item[side]["code"] == ap["code"]):
            # Routedatabase spreekt de positie tegen: alleen tonen wat we zeker weten.
            item["from"] = item["to"] = None
            item[side] = ap
    info = _aircraft_info(ac["hex"]) if ac.get("hex") else {}
    if not item["airline"]:
        item["airline"] = info.get("owner")
    if not item["aircraft_type"] and info.get("type"):
        item["aircraft_type"] = " ".join(x for x in (info.get("manufacturer"), info.get("type")) if x)
    for k in ("lat", "lon"):
        for side in ("from", "to"):
            if item[side]:
                item[side].pop(k, None)
    return item


@app.get("/api/overhead")
def overhead():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        abort(400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        abort(400)

    try:
        fetched_at, raw = _fetch_positions(lat, lon)
    except RuntimeError:
        return jsonify({"error": "Vliegtuigdata is nu even niet bereikbaar."}), 503

    airborne = []
    for ac in raw:
        alt_ft = _altitude_ft(ac)
        if alt_ft is None or ac.get("lat") is None or ac.get("lon") is None:
            continue
        if ac.get("t") in ("GND", "TWR") or str(ac.get("category", "")).startswith("C"):
            continue  # grondvoertuigen en vaste zenders
        ac = _extrapolate(ac, fetched_at)
        dist_m = max(_haversine_m(lat, lon, ac["lat"], ac["lon"]), 1.0)
        ac["_elev"] = round(math.degrees(math.atan2(max(alt_ft, 0) * 0.3048, dist_m)))
        ac["_dist"] = dist_m
        ac["_peak_elev"], ac["_peak_t"] = _pass_info(ac, alt_ft, lat, lon)
        airborne.append(ac)

    # Eerst wat nu boven je is (hoogst eerst), dan wat er zo aankomt, dan wat
    # net over kwam (meest recent eerst).
    now_ac = sorted((a for a in airborne if a["_elev"] >= MIN_ELEVATION_DEG), key=lambda a: -a["_elev"])
    # Hoogste punt op de rand van het venster = nog steeds aan het stijgen aan
    # de hemel (typisch een hoge, langzaam passerende vlieger). Dan is "komt
    # over 30 s langs" misleidend; die verschijnt vanzelf als "nu boven je".
    passing = [
        a for a in airborne
        if a["_elev"] < MIN_ELEVATION_DEG and a["_peak_elev"] >= MIN_ELEVATION_DEG
        and -LOOK_BACK_S < a["_peak_t"] < LOOK_AHEAD_S
    ]
    for a in passing:
        a["_passing"] = True
    coming = sorted((a for a in passing if a["_peak_t"] > 0), key=lambda a: a["_peak_t"])
    gone = sorted((a for a in passing if a["_peak_t"] < 0), key=lambda a: -a["_peak_t"])
    overhead_ac = (now_ac + coming + gone)[:MAX_RESULTS]
    nearest = None
    if not overhead_ac and airborne:
        nearest = _describe(min(airborne, key=lambda a: a["_dist"]), lat, lon, with_details=False)

    return jsonify(
        {
            "overhead": [_describe(a, lat, lon, with_details=True) for a in overhead_ac],
            "nearest": nearest,
            "time": int(time.time()),
        }
    )


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/manifest.json")
def manifest():
    return jsonify(
        {
            "name": "Wat vliegt daar?",
            "short_name": "Wat vliegt daar",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#bae6fd",
            "theme_color": "#0f172a",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
    )


@app.get("/sw.js")
def service_worker():
    # Lege service worker: alleen nodig zodat Chrome de app installeerbaar vindt.
    return Response("self.addEventListener('fetch', () => {});", mimetype="application/javascript")


@app.get("/icon-<int:size>.png")
def icon(size: int):
    if size not in (192, 512):
        abort(404)
    return send_from_directory(STATIC_DIR, f"icon-{size}.png")


@app.get("/icons/<name>")
def icons(name: str):
    # 3D-icoontjes van thiings.co (gratis licentie, naamsvermelding staat in de footer).
    return send_from_directory(STATIC_DIR / "icons", name, max_age=7 * 24 * 3600)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
