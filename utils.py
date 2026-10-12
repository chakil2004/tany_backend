import requests
import math
import logging

from datetime import date
from collections import defaultdict

import numpy as np
import requests_cache
import openmeteo_requests
from retry_requests import retry
from scipy.stats import gumbel_r

import requests
import math
import json
from datetime import date, datetime, timedelta
from statistics import mean, stdev
from math import radians, sin, cos, sqrt, atan2
import time


logger = logging.getLogger(__name__)


def get_terrain_slope(lat, lon, distance=30):
    # 1. Calculate the 8 surrounding points
    # 1 degree of lat approx 111,320 meters
    earth_radius = 6378137 
    lat_rad = math.radians(lat)
    
    # Offsets for 8 directions (0, 45, 90, ..., 315 degrees)
    points = [(lat, lon)] # Start with center
    for i in range(8):
        angle = math.radians(i * 45)
        
        # Calculate coordinate offsets
        d_lat = (distance * math.cos(angle)) / 111320
        d_lon = (distance * math.sin(angle)) / (111320 * math.cos(lat_rad))
        
        points.append((lat + d_lat, lon + d_lon))

    # 2. Batch request to API
    # Format: loc1_lat,loc1_lon|loc2_lat,loc2_lon...
    locations_str = "|".join([f"{p[0]},{p[1]}" for p in points])
    url = f"https://api.opentopodata.org/v1/srtm90m?locations={locations_str}"
    
    response = requests.get(url).json()
    elevations = [r['elevation'] for r in response['results']]
    
    center_elev = elevations[0]
    surrounding_elevs = elevations[1:]
    
    # 3. Calculate slopes
    # Slope = atan(rise / run) * (180 / pi)
    slopes = []
    for elev in surrounding_elevs:
        rise = elev - center_elev
        # Formula: degrees = atan(rise/run) * (180/pi)
        slope_deg = math.degrees(math.atan(abs(rise) / distance))
        slopes.append(slope_deg)
        
    return {
        "max_slope": max(slopes),
        "avg_slope": sum(slopes) / len(slopes),
        "individual_slopes": slopes
    }

def get_soil_data(longitude, latitude):
    """
    Fetches soil data from the ISRIC SoilGrids API and returns a flattened 
    dictionary of {property_depth : mean_value}.
    """
    soil = []
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"
    
    params = {
        'lon': longitude,
        'lat': latitude,
        'property': ['bdod', 'cec', 'cfvo', 'clay', 'nitrogen', 'phh2o', 'sand', 'silt', 'soc', 'ocd', 'wv0010', 'wv0033', 'wv1500'],
        'depth': ['0-5cm'],
        'value': 'mean'
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise an exception for bad status codes
        data = response.json()
        for i in data["properties"]["layers"]:
            #print(i["name"])
            soil.append({"name" : i["name"], 
                        "unit_measure" : i["unit_measure"]["mapped_units"],
                        "values" : i["depths"][0]["values"]["mean"]})

        
        return soil

    except requests.exceptions.RequestException as e:
        return {"error": f"Failed to fetch data: {str(e)}"}
    except (KeyError, AttributeError) as e:
        return {"error": f"Failed to parse API response: {str(e)}"}

def get_elevation(longitude, latitude):
    open_topo_api = "https://api.opentopodata.org/v1/srtm90m?locations={},{}&interpolation=cubic&format=json".format(latitude, longitude)
    try:
        # Send the GET request
        response = requests.get(open_topo_api)
        
        # Raise an exception if the request was unsuccessful
        response.raise_for_status()
        
        # Parse the JSON response
        data = response.json()
        
        # Access the elevation value
        # The structure is typically {'results': [{'elevation': 123.4, ...}], 'status': 'OK'}
        '''elevation addtiion'''
        elevation = data['results'][0]['elevation']
        return elevation

    except requests.exceptions.RequestException as e:
        print(f"An error occurred in elevation: {e}")
    except (KeyError, IndexError):
        print("Could not parse elevation data from the response.") 

###FLOOD###

# ---------------------------------------------------------------------------
# Open-Meteo client (module-level singleton — shared across requests)
# ---------------------------------------------------------------------------

def _build_client() -> openmeteo_requests.Client:
    session = requests_cache.CachedSession(".openmeteo_cache", expire_after=3600)
    session = retry(session, retries=5, backoff_factor=0.4)
    return openmeteo_requests.Client(session=session)


_om_client = _build_client()


# ---------------------------------------------------------------------------
# Core helpers (same logic as flood_risk.py)
# ---------------------------------------------------------------------------

def _fetch_discharge(lat: float, lon: float, start: str, end: str):
    import pandas as pd

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "river_discharge",
        "start_date": start,
        "end_date": end,
    }
    responses = _om_client.weather_api(
        "https://flood-api.open-meteo.com/v1/flood", params=params
    )
    response = responses[0]
    daily = response.Daily()
    variable = daily.Variables(0)

    n = variable.ValuesLength()
    times = (
        pd.date_range(
            start=pd.Timestamp(daily.Time(), unit="s"),
            end=pd.Timestamp(daily.TimeEnd(), unit="s"),
            freq=pd.Timedelta(seconds=daily.Interval()),
            inclusive="left",
        )
        .strftime("%Y-%m-%d")
        .tolist()
    )
    values = [variable.Values(i) for i in range(n)]
    clean = [(t, v) for t, v in zip(times, values) if v is not None and not np.isnan(v)]
    if not clean:
        return [], []
    dates, discharges = zip(*clean)
    return list(dates), list(discharges)


def _annual_maximums(dates, discharges):
    by_year = defaultdict(list)
    for t, v in zip(dates, discharges):
        by_year[t[:4]].append(v)
    return sorted(max(vals) for vals in by_year.values() if vals)


def _gumbel_thresholds(annual_max):
    mu, beta = gumbel_r.fit(annual_max)
    return_periods = [2, 5, 10, 20, 30, 50, 100]
    return {rp: float(gumbel_r.ppf(1 - 1 / rp, mu, beta)) for rp in return_periods}


def _classify_risk(peak, thresholds):
    levels = [
        (100, "extreme",   "≥ 100-year event — catastrophic flood very likely"),
        (50,  "very_high", "≥ 50-year event  — severe flood very likely"),
        (30,  "high",      "≥ 30-year event  — major flood likely"),
        (20,  "elevated",  "≥ 20-year event  — significant flood likely"),
        (10,  "moderate",  "≥ 10-year event  — notable flooding possible"),
        (5,   "low",       "≥ 5-year event   — minor flooding possible"),
        (2,   "watch",     "≥ 2-year event   — above-normal river levels"),
    ]
    for rp, label, description in levels:
        if peak >= thresholds.get(rp, float("inf")):
            return {
                "level": label,
                "return_period_exceeded": rp,
                "annual_probability_pct": round(100 / rp, 1),
                "description": description,
                "flooded": True,
            }
    return {
        "level": "normal",
        "return_period_exceeded": None,
        "annual_probability_pct": None,
        "description": "Below 2-year threshold — river levels within normal range",
        "flooded": False,
    }


def _no_river_response(lat: float, lon: float) -> dict:
    """
    Returned when no river is found within 5 km of the coordinate.
    Represents near-zero flood risk from riverine sources.
    """
    return {
        "coordinate": {"latitude": lat, "longitude": lon},
        "flood_risk": {
            "level": "negligible",
            "return_period_exceeded": None,
            "annual_probability_pct": 0.0,
            "description": (
                "No significant river detected within 5 km. "
                "Riverine flood risk is negligible at this location. "
                "Consider surface water or coastal flood sources separately."
            ),
            "flooded": False,
        },
        "forecast": {
            "peak_discharge_m3s": 0.0,
            "days_checked": None,
        },
        "statistics": {
            "years_analyzed": 0,
            "history_from": None,
            "thresholds_m3s": {},
            "note": "No river data available — statistics not applicable.",
        },
        "data_source": "GloFAS v4 via Open-Meteo (open-meteo.com)",
        "assessed_on": date.today().isoformat(),
    }


def assess_flood_risk(lat: float, lon: float, history_start: str, forecast_days: int) -> dict:
    today = date.today().isoformat()

    hist_dates, hist_vals = _fetch_discharge(lat, lon, history_start, today)
    if not hist_vals or max(hist_vals) < 0.01:
        raise ValueError("no_river")

    annual_max = _annual_maximums(hist_dates, hist_vals)
    if len(annual_max) < 5:
        raise ValueError("insufficient_history")

    thresholds = _gumbel_thresholds(annual_max)

    forecast_end = date.fromordinal(date.today().toordinal() + forecast_days).isoformat()
    _, forecast_vals = _fetch_discharge(lat, lon, today, forecast_end)
    peak = max(forecast_vals) if forecast_vals else hist_vals[-1]

    risk = _classify_risk(peak, thresholds)

    return {
        "coordinate": {"latitude": lat, "longitude": lon},
        "flood_risk": risk,
        "forecast": {
            "peak_discharge_m3s": round(peak, 2),
            "days_checked": forecast_days,
        },
        "statistics": {
            "years_analyzed": len(annual_max),
            "history_from": history_start,
            "thresholds_m3s": {str(rp): round(q, 2) for rp, q in thresholds.items()},
        },
        "data_source": "GloFAS v4 via Open-Meteo (open-meteo.com)",
        "assessed_on": today,
    }


def flood_risk(lat_str : str, lon_str: str):
    # --- Validate query params ---
    #lat_str = request.args.get("lat")
    #lon_str = request.args.get("lon")

    if lat_str is None or lon_str is None:
        return {
            "error": "missing_params",
            "message": "Both 'lat' and 'lon' query parameters are required.",
            "example": "/flood-risk?lat=-18.1492&lon=49.4023",
        }

    try:
        lat = float(lat_str)
        lon = float(lon_str)
    except ValueError:
        return {
            "error": "invalid_params",
            "message": "'lat' and 'lon' must be valid numbers.",
        }

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return {
            "error": "out_of_range",
            "message": "lat must be in [-90, 90] and lon in [-180, 180].",
        }

    history_start =  "1984-01-01"
    try:
        date.fromisoformat(history_start)
    except ValueError:
        return {
            "error": "invalid_date",
            "message": "'history_start' must be in YYYY-MM-DD format.",
        }

    try:
        forecast_days = 7 #int(request.args.get("forecast_days", 7))
        if not (1 <= forecast_days <= 7):
            raise ValueError
    except ValueError:
        return {
            "error": "invalid_forecast_days",
            "message": "'forecast_days' must be an integer between 1 and 7.",
        }

    # --- Run assessment ---
    try:
        result = assess_flood_risk(lat, lon, history_start, forecast_days)
        return result

    except ValueError as e:
        error_code = str(e)

        # No river nearby → return negligible risk (not an error)
        if error_code == "no_river":
            return _no_river_response(lat, lon)

        # Not enough historical years
        if error_code == "insufficient_history":
            return {
                "error": "insufficient_history",
                "message": (
                    "Less than 5 years of data found for this location. "
                    "Try setting history_start=1984-01-01."
                ),
                "coordinate": {"latitude": lat, "longitude": lon},
            }

        return {"error": "value_error", "message": error_code}

    except Exception as e:
        print("Unexpected error for (%s, %s)", lat, lon)
        return {
            "error": "internal_error",
            "message": "An unexpected error occurred. Check server logs.",
        }
    
##TEST##
#print(flood_risk("-18.1492", "49.4023")) #latitude=-18.1492, longitude=49.4023


###climate###
# ── helpers ───────────────────────────────────────────────────────────────────

def calculate_mean(arr):
    return mean(arr)

def stdev_sample(arr):
    return stdev(arr) if len(arr) >= 2 else 0.0

def cumulative_array(arr):
    result, total = [], 0.0
    for v in arr:
        total += v
        result.append(total)
    return result

def get_day_of_year(date_str):
    return datetime.strptime(date_str, "%Y%m%d").timetuple().tm_yday

def get_date_from_doy(year, doy):
    dt = datetime(year, 1, 1) + timedelta(days=int(doy) - 1)
    return {"month": dt.month, "day": dt.day}

def select_trunk(arr, start, length):
    return arr[start: start + length]

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

def transform_data(json_data):
    parameters = json_data["properties"]["parameter"]
    all_dates = list(next(iter(parameters.values())).keys())
    rows = []
    for date_key in all_dates:
        row = {"date": date_key}
        for param, values in parameters.items():
            row[param] = values.get(date_key)
        rows.append(row)
    return rows


def climate_stats(rows):
    monthly_t2m     = {m: [] for m in range(1, 13)}
    monthly_t2m_max = {m: [] for m in range(1, 13)}
    monthly_t2m_min = {m: [] for m in range(1, 13)}
    monthly_prec    = {}

    for r in rows:
        date_str = r["date"]
        month    = int(date_str[4:6])
        year     = int(date_str[:4])

        t2m     = r.get("T2M")
        t2m_max = r.get("T2M_MAX")
        t2m_min = r.get("T2M_MIN")
        prec    = r.get("PRECTOTCORR")

        if t2m is not None and t2m > -999:
            monthly_t2m[month].append(t2m)
        if t2m_max is not None and t2m_max > -999:
            monthly_t2m_max[month].append(t2m_max)
        if t2m_min is not None and t2m_min > -999:
            monthly_t2m_min[month].append(t2m_min)
        if prec is not None and prec >= 0:
            key = (year, month)
            monthly_prec[key] = monthly_prec.get(key, 0.0) + prec

    all_t2m = [v for vals in monthly_t2m.values() for v in vals]
    overall_mean_temp = round(mean(all_t2m), 2) if all_t2m else None

    month_max_means = {
        m: round(mean(vals), 2)
        for m, vals in monthly_t2m_max.items() if vals
    }
    hottest_month_num  = max(month_max_means, key=month_max_means.get)
    hottest_month_temp = month_max_means[hottest_month_num]

    month_min_means = {
        m: round(mean(vals), 2)
        for m, vals in monthly_t2m_min.items() if vals
    }
    coldest_month_num  = min(month_min_means, key=month_min_means.get)
    coldest_month_temp = month_min_means[coldest_month_num]

    month_totals = {m: [] for m in range(1, 13)}
    for (year, month), total in monthly_prec.items():
        month_totals[month].append(total)

    monthly_means_prec = {
        m: mean(vals) for m, vals in month_totals.items() if vals
    }
    avg_yearly_rain = round(sum(monthly_means_prec.values()), 1)

    wettest_month_num  = max(monthly_means_prec, key=monthly_means_prec.get)
    wettest_month_rain = round(monthly_means_prec[wettest_month_num], 1)

    return {
        "temperature": {
            "overall_mean_c":         overall_mean_temp,
            "hottest_month":          MONTH_NAMES[hottest_month_num],
            "hottest_month_num":      hottest_month_num,
            "hottest_month_avg_max_c": hottest_month_temp,
            "coldest_month":          MONTH_NAMES[coldest_month_num],
            "coldest_month_num":      coldest_month_num,
            "coldest_month_avg_min_c": coldest_month_temp,
        },
        "precipitation": {
            "avg_yearly_total_mm":  avg_yearly_rain,
            "wettest_month":        MONTH_NAMES[wettest_month_num],
            "wettest_month_num":    wettest_month_num,
            "wettest_month_avg_mm": wettest_month_rain,
        },
    }


# ── core analysis ─────────────────────────────────────────────────────────────

def season_analysis(longitude, latitude):
    today = date.today()
    end   = today.strftime("%Y%m%d")
    start = "19890101" if latitude > 0 else "19890701"

    url = (
        "https://power.larc.nasa.gov/api/temporal/daily/point"
        "?parameters=T2M,T2MDEW,T2MWET,TS,T2M_RANGE,T2M_MAX,T2M_MIN,PRECTOTCORR,EVLAND"
        "&community=RE"
        f"&longitude={longitude}&latitude={latitude}"
        f"&start={start}&end={end}&format=JSON"
    )

    print(f"  Fetching NASA POWER data for ({latitude}, {longitude})...")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    raw = resp.json()

    rows      = transform_data(raw)
    valid     = [r for r in rows if r.get("PRECTOTCORR") is not None and r["PRECTOTCORR"] >= 0]
    arr_prec  = [r["PRECTOTCORR"] for r in valid]
    day       = [r["date"]        for r in valid]

    net_year = round(len(arr_prec) / 365) - 1
    if net_year < 1:
        raise ValueError("Insufficient data to compute rainy season onset.")

    Y, d = 365, 0
    result_min, results_max, yearly_onsets = [], [], []

    for _ in range(net_year):
        trunked = select_trunk(arr_prec, d, Y)
        if len(trunked) < Y:
            break

        y_mean = calculate_mean(trunked)
        meaned = [v - y_mean for v in trunked]
        sd     = stdev_sample(meaned)
        if sd == 0:
            d += Y
            continue

        creduit     = [v / sd for v in meaned]
        result      = cumulative_array(creduit)
        fin_pluie   = result.index(max(result))
        debut_pluie = result.index(min(result))
        day_trunk   = select_trunk(day, d, Y)

        onset_doy = get_day_of_year(day_trunk[debut_pluie])
        end_doy   = get_day_of_year(day_trunk[fin_pluie])
        result_min.append(onset_doy)
        results_max.append(end_doy)

        year_int  = int(day_trunk[0][:4])
        onset_dt  = datetime(year_int, 1, 1) + timedelta(days=onset_doy - 1)
        end_dt    = datetime(year_int, 1, 1) + timedelta(days=end_doy - 1)
        yearly_onsets.append({
            "year":       year_int,
            "onset_doy":  onset_doy,
            "onset_date": onset_dt.strftime("%Y-%m-%d"),
            "end_doy":    end_doy,
            "end_date":   end_dt.strftime("%Y-%m-%d"),
        })
        d += Y

    if not result_min:
        raise ValueError("Not enough valid yearly slices to compute onset.")

    mean_onset_doy  = math.floor(calculate_mean(result_min))
    sd_onset_days   = math.floor(stdev_sample(result_min))
    onset_month_day = get_date_from_doy(today.year, mean_onset_doy)

    mean_end_doy  = math.floor(calculate_mean(results_max))
    sd_end_days   = math.floor(stdev_sample(results_max))
    end_month_day = get_date_from_doy(today.year, mean_end_doy)

    climate = climate_stats(rows)

    return {
        "latitude":   latitude,
        "longitude":  longitude,
        "data_range": {"start": start, "end": end},
        "rainy_season": {
            "onset": {
                "mean_month":   onset_month_day["month"],
                "mean_day":     onset_month_day["day"],
                "mean_doy":     mean_onset_doy,
                "std_dev_days": sd_onset_days,
                "interpretation": (
                    f"The rainy season typically starts around "
                    f"{onset_month_day['month']}/{onset_month_day['day']} (month/day), "
                    f"with a standard deviation of {sd_onset_days} days."
                ),
            },
            "end": {
                "mean_month":   end_month_day["month"],
                "mean_day":     end_month_day["day"],
                "mean_doy":     mean_end_doy,
                "std_dev_days": sd_end_days,
                "interpretation": (
                    f"The rainy season typically ends around "
                    f"{end_month_day['month']}/{end_month_day['day']} (month/day), "
                    f"with a standard deviation of {sd_end_days} days."
                ),
            },
        },
        "temperature":   climate["temperature"],
        "precipitation": climate["precipitation"],
    }


# ── proximity helpers ─────────────────────────────────────────────────────────

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi    = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def query_overpass(query, retries=3):
    for server in OVERPASS_SERVERS:
        for attempt in range(retries):
            try:
                response = requests.get(
                    server,
                    params={"data": query},
                    timeout=30,
                    headers={"User-Agent": "ProximityChecker/1.0"},
                )
                if response.status_code == 429:
                    time.sleep(10)
                    continue
                if response.status_code != 200 or not response.text.strip():
                    break
                return response.json()
            except requests.exceptions.Timeout:
                time.sleep(2)
            except requests.exceptions.JSONDecodeError:
                break
    return None


def build_query(lat, lon, radius):
    return f"""
    [out:json][timeout:30];
    (
      node["amenity"="hospital"](around:{radius},{lat},{lon});
      way["amenity"="hospital"](around:{radius},{lat},{lon});
      node["amenity"="clinic"](around:{radius},{lat},{lon});

      node["shop"="supermarket"](around:{radius},{lat},{lon});
      way["shop"="supermarket"](around:{radius},{lat},{lon});
      node["shop"="convenience"](around:{radius},{lat},{lon});

      node["highway"="bus_stop"](around:{radius},{lat},{lon});
      node["railway"="station"](around:{radius},{lat},{lon});
      node["railway"="tram_stop"](around:{radius},{lat},{lon});
      node["public_transport"="station"](around:{radius},{lat},{lon});
    );
    out center;
    """


def parse_elements(elements, lat, lon):
    results = {"hospital": [], "market": [], "transport": []}

    for el in elements:
        tags   = el.get("tags", {})
        name   = tags.get("name", "Unnamed")
        el_lat = el.get("lat") or el.get("center", {}).get("lat")
        el_lon = el.get("lon") or el.get("center", {}).get("lon")

        if not el_lat:
            continue

        distance  = round(haversine(lat, lon, el_lat, el_lon))
        amenity   = tags.get("amenity", "")
        shop      = tags.get("shop", "")
        highway   = tags.get("highway", "")
        railway   = tags.get("railway", "")
        public_tr = tags.get("public_transport", "")

        entry = {
            "name":       name,
            "distance_m": distance,
            "lat":        el_lat,
            "lon":        el_lon,
        }

        if amenity in ("hospital", "clinic"):
            results["hospital"].append(entry)
        elif shop in ("supermarket", "convenience"):
            results["market"].append(entry)
        elif highway == "bus_stop" or railway in ("station", "tram_stop") or public_tr == "station":
            results["transport"].append(entry)

    for key in results:
        results[key].sort(key=lambda x: x["distance_m"])

    return results


def proximity_analysis(latitude, longitude, radius=5000):
    """
    Query Overpass for hospitals/clinics, markets, and transport
    within `radius` metres of (latitude, longitude).
    """
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        raise ValueError("lat must be in [-90, 90] and lon in [-180, 180].")
    if not (100 <= radius <= 10000):
        raise ValueError("radius must be between 100 and 10000 metres.")

    print(f"  Querying Overpass for ({latitude}, {longitude}), radius={radius}m ...")
    data = query_overpass(build_query(latitude, longitude, radius))

    if data is None:
        raise RuntimeError("Overpass API unavailable. Try again later.")

    results = parse_elements(data.get("elements", []), latitude, longitude)

    return {
        "query":   {"lat": latitude, "lon": longitude, "radius_m": radius},
        "results": results,
    }


#test 
#print(season_analysis(49.4023, -18.1492))
#print(proximity_analysis(-18.1492, 49.4023))

''' LIST OF TH FUNCTION THAT NEEDS TO BE ENTERED IN THE json
1. ELEVATION : get_elevation(longitude, latitude)
2. pentes : get_terrain_slope(lat, lon, distance=30)
3. soil : get_soil_data(longitude, latitude)
4. flood risk : flood_risk(lat_str : str, lon_str: str)
5. season : season_analysis(longitude, latitude)
6. proximity analysis : proximity_analysis(latitude, longitude, radius=5000)
'''

#elevation test
#print(get_elevation(49.4023, -18.1492))
#print(get_terrain_slope(-18.1492, 49.4023, distance=30))
#print(get_soil_data(49.4023, -18.1492))

class get_all_infos:
    def __init__(self, longitude: float, latitude : float, uuid_parcel):
        self.longitude = longitude
        self.latitude = latitude
        self.uuid_parcel = uuid_parcel

    def all_data(self):
        value = {}
        ers = {}
        #elevation 
        try:
            el = get_elevation(self.longitude, self.latitude)
            if isinstance(el, (int, float)) and not isinstance(el, bool) and math.isfinite(el):
                value["elevation"] = el
                ers["elevation"] = "ok!"
            else:
                value["elevation"] = None
                ers["elevation"] = "OpenTopoData returned no valid elevation value"
                logger.warning("OpenTopoData elevation returned no valid numeric value")
        except requests.exceptions.RequestException as e:
            value["elevation"] = None
            ers["elevation"] = f"An error occurred in elevation: {e}"
            logger.warning("OpenTopoData elevation request failed: %s", e)
        except (KeyError, IndexError):
            value["elevation"] = None
            ers["elevation"] = "Could not parse elevation data from the response. (KeyError, IndexError)"
            logger.warning("Could not parse OpenTopoData elevation response")
        
        #slopes
        try:
            slopes = get_terrain_slope(self.latitude, self.longitude, distance=30)
            value.update(slopes)
            ers["slopes"] = "ok!"
        except requests.exceptions.RequestException as e:
            print(f"An error occurred in slopes: {e}")
            #value.update(balise)
            ers["slopes"] = f"An error occurred in slopes: {e}"
        except (KeyError, IndexError):
            print("Could not parse slopes data from the response.")
            ers["slopes"] = "Could not parse slopes data from the response. (KeyError, IndexError)"
        
        #soil
        try:
            soil = get_soil_data(self.longitude, self.latitude)
            soil_is_valid = (
                isinstance(soil, list)
                and bool(soil)
                and all(
                    isinstance(item, dict)
                    and {"name", "unit_measure", "values"}.issubset(item)
                    and (
                        item["values"] is None
                        or (
                            isinstance(item["values"], (int, float))
                            and not isinstance(item["values"], bool)
                            and math.isfinite(item["values"])
                        )
                    )
                    for item in soil
                )
                and any(item["values"] is not None for item in soil)
            )
            if soil_is_valid:
                value["soil_value"] = soil
                ers["soil"] = "ok!"
            else:
                value["soil_value"] = []
                soil_error = (
                    str(soil["error"])
                    if isinstance(soil, dict) and "error" in soil
                    else "SoilGrids returned no valid soil data"
                )
                ers["soil"] = soil_error
                ers["soil_value"] = soil_error
                logger.warning("SoilGrids data unavailable: %s", soil_error)
        except requests.exceptions.RequestException as e:
            value["soil_value"] = []
            soil_error = f"An error occurred in soil: {e}"
            ers["soil"] = soil_error
            ers["soil_value"] = soil_error
            logger.warning("SoilGrids request failed: %s", e)
        except (KeyError, IndexError):
            value["soil_value"] = []
            soil_error = "Could not parse soil data from the response. (KeyError, IndexError)"
            ers["soil"] = soil_error
            ers["soil_value"] = soil_error
            logger.warning("Could not parse SoilGrids response")

        #flood
        try:
            flood = flood_risk(lon_str=str(self.longitude), lat_str= str(self.latitude))
            flood_is_valid = (
                isinstance(flood, dict)
                and "error" not in flood
                and isinstance(flood.get("coordinate"), dict)
                and isinstance(flood.get("flood_risk"), dict)
                and isinstance(flood.get("forecast"), dict)
                and isinstance(flood.get("statistics"), dict)
            )
            if flood_is_valid:
                value["flood"] = flood
                ers["flood"] = "ok!"
            else:
                value["flood"] = None
                flood_error = (
                    str(flood["error"])
                    if isinstance(flood, dict) and "error" in flood
                    else "Flood service returned no valid flood data"
                )
                ers["flood"] = flood_error
                logger.warning("Flood risk data unavailable: %s", flood_error)
        except requests.exceptions.RequestException as e:
            value["flood"] = None
            ers["flood"] = f"An error occurred in flood: {e}"
            logger.warning("Flood risk request failed: %s", e)
        except (KeyError, IndexError):
            value["flood"] = None
            ers["flood"] = "Could not parse flood data from the response. (KeyError, IndexError)"
            logger.warning("Could not parse flood risk response")

        #climate
        try:
            climate = season_analysis(self.longitude, self.latitude)
            value["climates"] = climate
            ers["climates"] = "ok!"
        except requests.exceptions.RequestException as e:
            print(f"An error occurred in climates: {e}")
            ers["climates"] = f"An error occurred in climates: {e}"
        except (KeyError, IndexError):
            print("Could not parse climates data from the response.") 
            ers["climates"] = "Could not parse climates data from the response. (KeyError, IndexError)"

        #proximity
        try:
            proximi = proximity_analysis(self.latitude, self.longitude, radius=5000)
            proximity_results = proximi.get("results") if isinstance(proximi, dict) else None
            proximity_is_valid = (
                isinstance(proximi, dict)
                and isinstance(proximi.get("query"), dict)
                and isinstance(proximity_results, dict)
                and all(
                    isinstance(proximity_results.get(category), list)
                    for category in ("hospital", "market", "transport")
                )
            )
            if proximity_is_valid:
                value["proximity"] = proximi
                ers["proximity"] = "ok!"
            else:
                value["proximity"] = None
                ers["proximity"] = "Overpass returned no valid proximity data"
                logger.warning("Overpass returned an invalid proximity response")
        except (RuntimeError, requests.exceptions.RequestException) as e:
            value["proximity"] = None
            ers["proximity"] = f"An error occurred in proximity: {e}"
            logger.warning("Overpass proximity query failed: %s", e)
        
        return [
            self.uuid_parcel,
            value,
            ers
        ]
    ''' list of 
    [
    uuid of the parcel, 
    dict with all the expected value need to be tranformed into json,
    disct of logs need to be transformed into json
    ]
    '''

#BIG TEST
#test = get_all_infos(49.4023, -18.1492, "uuid_test")
#print(test.all_data())
