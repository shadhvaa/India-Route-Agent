"""Tools the agent can call. Each tool is a plain Python function that returns a dict."""
import math
import re
import time
from functools import lru_cache
from pathlib import Path

import requests

HEADERS = {"User-Agent": "india-route-agent/1.0 (student project)"}
TIMEOUT = 10
PLANS_DIR = Path("plans")


# ---------- helpers ----------
def _haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=128)
def _geocode(place: str):
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1, "countrycodes": "in"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    data = resp.json()
    if not data:
        return None
    d = data[0]
    return {"name": d["display_name"], "lat": float(d["lat"]), "lon": float(d["lon"])}


def _safe(fn):
    """Return an error dict instead of raising, so the agent can recover."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{fn.__name__} failed: {e}"}
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------- tools ----------
@_safe
def geocode_place(place: str):
    loc = _geocode(place)
    if not loc:
        return {"error": f"Could not find '{place}' in India. Try adding the state, e.g. 'Hosur, Tamil Nadu'."}
    return loc


@_safe
def get_driving_route(origin: str, destination: str):
    o, d = _geocode(origin), _geocode(destination)
    if not o or not d:
        return {"error": "Could not geocode origin or destination. Add the state name and retry."}
    url = f"https://router.project-osrm.org/route/v1/driving/{o['lon']},{o['lat']};{d['lon']},{d['lat']}"
    r = requests.get(url, params={"overview": "false"}, headers=HEADERS, timeout=TIMEOUT).json()
    if r.get("code") != "Ok":
        return {"error": f"Routing failed: {r.get('message', r.get('code'))}"}
    route = r["routes"][0]
    return {
        "origin": o["name"],
        "destination": d["name"],
        "road_distance_km": round(route["distance"] / 1000, 1),
        "driving_time_hours": round(route["duration"] / 3600, 1),
        "straight_line_km": round(_haversine(o["lat"], o["lon"], d["lat"], d["lon"]), 1),
    }


@_safe
def suggest_halts(origin: str, destination: str, interval_km: int = 150):
    """Pick break-journey towns roughly every `interval_km` along the driving route."""
    o, d = _geocode(origin), _geocode(destination)
    if not o or not d:
        return {"error": "Could not geocode origin or destination."}
    url = f"https://router.project-osrm.org/route/v1/driving/{o['lon']},{o['lat']};{d['lon']},{d['lat']}"
    r = requests.get(url, params={"overview": "full", "geometries": "geojson"}, headers=HEADERS, timeout=TIMEOUT).json()
    if r.get("code") != "Ok":
        return {"error": "Routing failed."}
    coords = r["routes"][0]["geometry"]["coordinates"]  # [lon, lat]
    total_km = r["routes"][0]["distance"] / 1000
    if total_km < interval_km * 1.3:
        return {"halts": [], "note": f"Route is only {total_km:.0f} km; no halt needed."}

    targets = []
    k = interval_km
    while k < total_km - interval_km * 0.4 and len(targets) < 5:
        targets.append(k)
        k += interval_km

    halts, cum, ti = [], 0.0, 0
    for i in range(1, len(coords)):
        cum += _haversine(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0])
        if ti < len(targets) and cum >= targets[ti]:
            lon, lat = coords[i]
            time.sleep(1.1)  # Nominatim usage policy: max 1 request/sec
            rev = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
                headers=HEADERS,
                timeout=TIMEOUT,
            ).json()
            addr = rev.get("address", {})
            name = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or "Unnamed place"
            halts.append({"approx_km_from_start": round(targets[ti]), "place": name, "district_or_state": addr.get("state_district") or addr.get("state", "")})
            ti += 1
    return {"total_route_km": round(total_km), "halts": halts}


@_safe
def get_weather(place: str, days: int = 3):
    loc = _geocode(place)
    if not loc:
        return {"error": f"Could not find '{place}'."}
    days = max(1, min(int(days), 7))
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Kolkata",
            "forecast_days": days,
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    ).json()
    daily = r["daily"]
    out = []
    for i, day in enumerate(daily["time"]):
        out.append({
            "date": day,
            "max_c": daily["temperature_2m_max"][i],
            "min_c": daily["temperature_2m_min"][i],
            "rain_chance_pct": daily["precipitation_probability_max"][i],
        })
    return {"place": loc["name"], "forecast": out}


@_safe
def compare_travel_modes(road_distance_km: float, passengers: int = 1):
    """Rule-based estimates (assumptions listed in README). Not live fares."""
    km = float(road_distance_km)
    pax = max(1, int(passengers))
    rail_km, air_km = km * 1.05, km * 0.8

    # (mode, hours, cost per person, note)
    car_total = km / 15 * 105 + km * 0.8  # 15 km/l, Rs105/l petrol, ~Rs0.8/km toll+misc
    modes = [
        {"mode": "Car (self-drive/cab share)", "hours": round(km / 50, 1), "total_cost_inr": round(car_total), "cost_per_person_inr": round(car_total / pax), "note": "Fuel + approx tolls; cab hire costs more."},
        {"mode": "Bus (ordinary to AC sleeper)", "hours": round(km / 45, 1), "cost_per_person_inr": f"{round(km * 1.2)}-{round(km * 2.5)}", "note": "Range from state-transport ordinary to private AC sleeper."},
        {"mode": "Train (Sleeper to 2AC)", "hours": round(rail_km / 55, 1), "cost_per_person_inr": f"{round(rail_km * 0.55)}-{round(rail_km * 2.0)}", "note": "Depends on class and train type; check availability on IRCTC."},
    ]
    if km >= 300:
        modes.append({"mode": "Flight (economy)", "hours": round(air_km / 700 + 3.5, 1), "cost_per_person_inr": f"{round(max(3000, air_km * 4))}-{round(max(6000, air_km * 8))}", "note": "Includes ~3.5h for airport transfers, check-in and security."})
    else:
        modes.append({"mode": "Flight (economy)", "hours": None, "cost_per_person_inr": None, "note": "Not practical under 300 km."})
    return {"road_distance_km": km, "passengers": pax, "modes": modes, "disclaimer": "Estimates only, not live fares or schedules."}


@_safe
def save_trip_plan(title: str, content: str):
    PLANS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "trip-plan"
    path = PLANS_DIR / f"{slug}.md"
    path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return {"saved_to": str(path)}


# ---------- registry + schemas for the LLM ----------
TOOL_FUNCTIONS = {
    "geocode_place": geocode_place,
    "get_driving_route": get_driving_route,
    "suggest_halts": suggest_halts,
    "get_weather": get_weather,
    "compare_travel_modes": compare_travel_modes,
    "save_trip_plan": save_trip_plan,
}


def _schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOL_SCHEMAS = [
    _schema("geocode_place", "Find latitude/longitude and full name of a place in India. Use to verify ambiguous place names.", {"place": {"type": "string", "description": "e.g. 'Coimbatore, Tamil Nadu'"}}, ["place"]),
    _schema("get_driving_route", "Get road distance (km) and driving time (hours) between two places in India.", {"origin": {"type": "string"}, "destination": {"type": "string"}}, ["origin", "destination"]),
    _schema("suggest_halts", "Suggest break-journey towns along the driving route, roughly every interval_km. Use for road trips over ~300 km.", {"origin": {"type": "string"}, "destination": {"type": "string"}, "interval_km": {"type": "integer", "description": "Default 150"}}, ["origin", "destination"]),
    _schema("get_weather", "Get a short daily forecast (temperature, rain chance) for a place in India.", {"place": {"type": "string"}, "days": {"type": "integer", "description": "1-7, default 3"}}, ["place"]),
    _schema("compare_travel_modes", "Estimate time and cost for car, bus, train and flight for a given road distance. Call after get_driving_route.", {"road_distance_km": {"type": "number"}, "passengers": {"type": "integer", "description": "Default 1"}}, ["road_distance_km"]),
    _schema("save_trip_plan", "Save the final trip plan as a markdown file. Only call if the user asks to save.", {"title": {"type": "string"}, "content": {"type": "string"}}, ["title", "content"]),
]
