"""Bounded public weather adapters preserving receipt time and source authority.

MADIS temperature units and XML fields follow the official Surface Viewer docs.
The public APRSWXNET subset is CWOP. Neither it nor AWC proxy observations are
accepted here as the exact contract's settlement population or finality source.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

from .evidence import EvidenceError, EvidenceStore, digest, finite, identity, sha


MADIS_ENDPOINT = "https://madis-data.ncep.noaa.gov/madisPublic1/cgi-bin/madisXmlPublicDir"
MADIS_FIXED = {"time":"0", "minfwd":"0", "recwin":"3", "timefilter":"0", "dfltrsel":"1",
               "stasel":"0", "pvdrsel":"1", "pvd":"APRSWXNET", "varsel":"1", "nvars":"T",
               "qctype":"0", "qcsel":"1", "xml":"1", "csvmiss":"0"}

# Permitted live PWS source #2 (owner-approved; MADIS guest access is held).
# Collected offline by a separate cron collector into retained JSON artifacts;
# this module never performs HTTP to this or any host (see tools/ ingest CLI).
XWEATHER_ENDPOINT = "https://data.api.xweather.com/observations/within"
XWEATHER_FILTER = "pws"
MADIS_ADAPTER_VERSION = "alpha_v11_madis_public_xml_v1"
XWEATHER_ADAPTER_VERSION = "alpha_v11_xweather_pws_json_v1"
# Closed PWS provider allow-list. A new transport family needs a code/review
# change here, not implicit trust in a persisted provider string.
PWS_PROVIDERS = {
    "NOAA_MADIS_CWOP": {"adapter_version": MADIS_ADAPTER_VERSION, "channel_prefix": "CWOP_NEAR:"},
    "XWEATHER_PWSWEATHER": {"adapter_version": XWEATHER_ADAPTER_VERSION, "channel_prefix": "XWEATHER_NEAR:"},
}


def numeric(value, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise EvidenceError("SOURCE_NUMBER_INVALID")
    try:
        return finite(float(value), nonnegative=nonnegative)
    except (ValueError, OverflowError):
        raise EvidenceError("SOURCE_NUMBER_INVALID") from None


def validate_madis_params(params):
    if (set(params) != set(MADIS_FIXED) | {"minbck", "latll", "lonll", "latur", "lonur"}
            or any(params[k] != v for k,v in MADIS_FIXED.items())):
        raise EvidenceError("MADIS_PUBLIC_SUBSET_REQUIRED")
    if not -60 <= numeric(params["minbck"]) <= -1:
        raise EvidenceError("MADIS_TIME_WINDOW_BOUND")
    south,west,north,east = (numeric(params[k]) for k in ("latll","lonll","latur","lonur"))
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180
            and north-south <= 1 and east-west <= 1):
        raise EvidenceError("MADIS_GEOGRAPHIC_BOUND")


def madis_request(*, event_id: str, station: str, latitude: float, longitude: float,
                  half_width_degrees: float = .25, lookback_minutes: int = 30):
    from .collection import SourceRequest
    lat,lon,width = numeric(latitude),numeric(longitude),numeric(half_width_degrees,nonnegative=True)
    if width == 0 or width > .5 or type(lookback_minutes) is not int:
        raise EvidenceError("MADIS_GEOGRAPHIC_OR_TIME_BOUND")
    params = dict(MADIS_FIXED, minbck=str(-lookback_minutes), latll=str(lat-width),
                  lonll=str(lon-width), latur=str(lat+width), lonur=str(lon+width))
    validate_madis_params(params)
    return SourceRequest(provider="NOAA_MADIS_CWOP",url=MADIS_ENDPOINT,event_id=event_id,
                         kind="PWS_OBSERVATION",source_identity="CWOP_NEAR:"+identity(station),
                         revision="LOCAL_RECEIPT",params=tuple(sorted(params.items())),response_format="MADIS_XML")


def parse_madis_xml(raw: str, *, received_at: float, params: dict,
                    max_records: int = 1000) -> dict:
    validate_madis_params(params)
    receipt = finite(received_at)
    if (not isinstance(raw, str) or len(raw.encode()) > 768*1024
            or type(max_records) is not int or not 1 <= max_records <= 1000):
        raise EvidenceError("MADIS_RESPONSE_BOUND")
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise EvidenceError("MADIS_XML_DECLARATION_REFUSED")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise EvidenceError("MADIS_XML_INVALID") from None
    if root.tag != "mesonet" or len(root) > max_records:
        raise EvidenceError("MADIS_ROOT_OR_RECORD_BOUND")
    observations,rejected,seen = [],Counter(),set()
    for item in root:
        try:
            a = item.attrib
            if item.tag != "record" or len(item) or a.get("var") != "V-T" or a.get("provider") != "APRSWXNET":
                raise EvidenceError("MADIS_PROVIDER_OR_VARIABLE_MISMATCH")
            station = identity(a["shef_id"], maximum=32)
            if not re.fullmatch(r"[A-Z0-9_-]+", station):
                raise EvidenceError("MADIS_STATION_INVALID")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", a["ObTime"]):
                raise EvidenceError("MADIS_OBSERVATION_TIME_FORMAT")
            observed = datetime.fromisoformat(a["ObTime"]).replace(tzinfo=timezone.utc).timestamp()
            if observed > receipt or receipt-observed > -numeric(params["minbck"])*60+60:
                raise EvidenceError("MADIS_STALE_OR_FUTURE_OBSERVATION")
            lat,lon,elevation,temp = (numeric(a[k]) for k in ("lat","lon","elev","data_value"))
            if not (numeric(params["latll"]) <= lat <= numeric(params["latur"])
                    and numeric(params["lonll"]) <= lon <= numeric(params["lonur"])
                    and -500 <= elevation <= 9000 and 180 <= temp <= 340):
                raise EvidenceError("MADIS_LOCATION_OR_PHYSICAL_RANGE")
            qca,qcr = int(a["QCA"]),int(a["QCR"])
            if not 0 <= qca < 2**32 or not 0 <= qcr < 2**32:
                raise EvidenceError("MADIS_QC_MASK_INVALID")
            key = (station,observed)
            if key in seen:
                raise EvidenceError("MADIS_DUPLICATE_STATION_TIME")
            seen.add(key)
            value = {"station":station,"provider":"APRSWXNET","latitude":lat,"longitude":lon,
                     "elevation_m":elevation,"temperature_k":temp,"temperature_c":temp-273.15,
                     "observed_at":observed,"provider_published_at":None,"provider_received_at":None,
                     "local_received_at":receipt,"age_at_receipt_seconds":receipt-observed,
                     "provider_qc_descriptor":identity(a["QCD"],maximum=8),
                     "provider_qc_applied":qca,"provider_qc_results":qcr,
                     "provider_checks_without_failures":qca>0 and qcr==0,
                     "local_qc_certified":False,"station_representativeness_certified":False,
                     "settlement_authority":False,"calibration_label_authority":False,
                     "financial_authority":False,"source_role":"PWS_AUXILIARY_ONLY"}
            value["observation_key"] = digest({"provider":"APRSWXNET","station":station,"observed_at":observed})
            value["observation_identity"] = digest({k:v for k,v in value.items()
                                                   if k not in {"local_received_at","age_at_receipt_seconds"}})
            observations.append(value)
        except (EvidenceError, KeyError, ValueError, TypeError, OverflowError) as exc:
            code = str(exc) if isinstance(exc, EvidenceError) else "MADIS_RECORD_SCHEMA_INVALID"
            rejected[code] += 1
    return {"adapter_version":"alpha_v11_madis_public_xml_v1", "observations":observations,
            "rejections":dict(rejected),"response_records":len(root),
            "financial_authority":False,"settlement_authority":False,
            "lead_advantage_verified":False,"complete_geographic_coverage":False}


def parse_awc_metar(payload, *, station: str, received_at: float, max_age_seconds: float) -> dict:
    from .metar_features import parse_metar_physical
    receipt,max_age = finite(received_at),finite(max_age_seconds)
    if not isinstance(payload, list) or len(payload)>400 or max_age<=0:
        raise EvidenceError("AWC_RESPONSE_OR_AGE_BOUND")
    observations,rejections = [],Counter()
    for item in payload:
        try:
            if not isinstance(item,dict) or item.get("icaoId") != station:
                raise EvidenceError("AWC_STATION_MISMATCH")
            observed = numeric(item["obsTime"],nonnegative=True)
            temp = numeric(item["temp"])
            if not 0 <= receipt-observed <= max_age or not -90 <= temp <= 60:
                raise EvidenceError("AWC_STALE_FUTURE_OR_RANGE")
            observations.append({"station":station,"observed_at":observed,"local_received_at":receipt,
                                 "temperature_c":temp,"raw_metar":item.get("rawOb"),
                                 "physical_context":parse_metar_physical(item.get("rawOb"),station=station,observed_at=observed),
                                 "provider_report_time":item.get("reportTime"),
                                 "source_role":"OFFICIAL_METAR_PROXY_NOT_EXACT_CONTRACT_POPULATION",
                                 "settlement_authority":False,"calibration_label_authority":False,
                                 "financial_authority":False})
        except (EvidenceError, KeyError, ValueError, TypeError) as exc:
            rejections[str(exc) if isinstance(exc,EvidenceError) else "AWC_SCHEMA_INVALID"] += 1
    return {"adapter_version":"alpha_v11_awc_metar_v2_physical_body","observations":observations,
            "rejections":dict(rejections),"settlement_authority":False,"financial_authority":False}


def _haversine_km(lat1, lon1, lat2, lon2):
    a,b = math.radians(lat1),math.radians(lat2)
    dlat,dlon = math.radians(lat2-lat1),math.radians(lon2-lon1)
    h = math.sin(dlat/2)**2+math.cos(a)*math.cos(b)*math.sin(dlon/2)**2
    return 6371.0088*2*math.asin(min(1.,math.sqrt(max(0.,h))))


def validate_xweather_params(params, *, latitude, longitude, maximum_radius_km=80.):
    if (not isinstance(params,dict) or set(params) != {"p","radius","filter","limit"}
            or params.get("filter") != XWEATHER_FILTER):
        raise EvidenceError("XWEATHER_PARAMS_INVALID")
    if not re.fullmatch(r"[1-9][0-9]{0,2}", str(params.get("limit"))) or int(params["limit"]) > 100:
        raise EvidenceError("XWEATHER_LIMIT_BOUND")
    match = re.fullmatch(r"([1-9][0-9]{0,2})miles", str(params.get("radius")))
    if not match:
        raise EvidenceError("XWEATHER_RADIUS_FORMAT")
    radius_km = int(match.group(1))*1.609344
    if not 0 < radius_km <= maximum_radius_km:
        raise EvidenceError("XWEATHER_RADIUS_BOUND")
    parts = str(params.get("p","")).split(",")
    if len(parts) != 2:
        raise EvidenceError("XWEATHER_CENTER_FORMAT")
    lat,lon = numeric(parts[0]),numeric(parts[1])
    if abs(lat-latitude) > 1e-3 or abs(lon-longitude) > 1e-3:
        raise EvidenceError("XWEATHER_CENTER_MISMATCH")
    return radius_km


def parse_xweather_json(raw_body: bytes, *, raw_sha256: str, received_at: float, params: dict,
                        latitude: float, longitude: float, max_records: int = 200,
                        max_observation_age_seconds: float = 3600.) -> dict:
    """Parse the RAW Xweather `observations/within` JSON body, never the
    collector's own `projection`. Same identity/bounds discipline as MADIS:
    finite physical ranges, stale/future rejection, duplicate station/time
    rejection, geographic containment and response/record bounds. Provider QC
    fields (code, trust factor, data source) are preserved informationally,
    never treated as a calibrated QC certification.
    """
    receipt = finite(received_at)
    if not isinstance(raw_body,bytes) or len(raw_body) > 768*1024:
        raise EvidenceError("XWEATHER_RESPONSE_BOUND")
    if hashlib.sha256(raw_body).hexdigest() != sha(raw_sha256):
        raise EvidenceError("XWEATHER_HASH_MISMATCH")
    if type(max_records) is not int or not 1 <= max_records <= 200:
        raise EvidenceError("XWEATHER_RESPONSE_BOUND")
    if not 60 <= finite(max_observation_age_seconds) <= 86400:
        raise EvidenceError("XWEATHER_AGE_POLICY_BOUND")
    radius_km = validate_xweather_params(params, latitude=latitude, longitude=longitude)
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceError("XWEATHER_JSON_INVALID") from None
    if type(data) is not dict or data.get("success") is not True:
        raise EvidenceError("XWEATHER_RESPONSE_SHAPE_INVALID")
    rows = data.get("response")
    if type(rows) is dict:
        rows = [rows]
    if type(rows) is not list or len(rows) > max_records:
        raise EvidenceError("XWEATHER_RESPONSE_OR_RECORD_BOUND")
    observations,rejected,seen = [],Counter(),set()
    for row in rows:
        try:
            if type(row) is not dict:
                raise EvidenceError("XWEATHER_RECORD_SCHEMA_INVALID")
            sid = row.get("id")
            # Bound matches PWSSample's station identity so one unrepresentable
            # provider id is a per-row rejection, never a whole-neighborhood abort.
            if not isinstance(sid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}",sid):
                raise EvidenceError("XWEATHER_STATION_ID_INVALID")
            station = identity(sid,maximum=32)
            ob,loc,profile = row.get("ob"),row.get("loc"),row.get("profile")
            if type(ob) is not dict or type(loc) is not dict or type(profile) is not dict:
                raise EvidenceError("XWEATHER_RECORD_SCHEMA_INVALID")
            if row.get("dataSource") != "PWS" or ob.get("type") != "station":
                raise EvidenceError("XWEATHER_NON_PWS_STATION_RECORD")
            lat,lon = numeric(loc.get("lat")),numeric(loc.get("long"))
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise EvidenceError("XWEATHER_LOCATION_INVALID")
            if _haversine_km(latitude,longitude,lat,lon) > radius_km+1.0:
                raise EvidenceError("XWEATHER_OUTSIDE_RADIUS")
            elevation_m = numeric(profile.get("elevM"),nonnegative=False)
            if not -500 <= elevation_m <= 9000:
                raise EvidenceError("XWEATHER_ELEVATION_INVALID")
            observed = numeric(ob.get("timestamp"),nonnegative=True)
            if observed > receipt or receipt-observed > max_observation_age_seconds:
                raise EvidenceError("XWEATHER_STALE_OR_FUTURE_OBSERVATION")
            temp_c = numeric(ob.get("tempC"),nonnegative=False)
            if not -60 <= temp_c <= 60:
                raise EvidenceError("XWEATHER_PHYSICAL_RANGE")
            qc_code = numeric(ob.get("QCcode"),nonnegative=True)
            trust = numeric(ob.get("trustFactor"),nonnegative=True)
            if not 0 <= qc_code < 1000 or not 0 <= trust <= 100:
                raise EvidenceError("XWEATHER_PROVIDER_QC_INVALID")
            provider_received = None
            rec_ts = ob.get("recTimestamp")
            if rec_ts is not None:
                provider_received = numeric(rec_ts,nonnegative=True)
            key = (station,observed)
            if key in seen:
                raise EvidenceError("XWEATHER_DUPLICATE_STATION_TIME")
            seen.add(key)
            qc_applied = 1
            qc_results = 0 if (qc_code == 10 and trust >= 80) else 1
            value = {"station":station,"provider":"PWSWEATHER","latitude":lat,"longitude":lon,
                     "elevation_m":elevation_m,"temperature_k":temp_c+273.15,"temperature_c":temp_c,
                     "observed_at":observed,"provider_published_at":None,
                     "provider_received_at":provider_received,"local_received_at":receipt,
                     "age_at_receipt_seconds":receipt-observed,
                     "provider_qc_descriptor":identity(str(ob.get("QC") or "UNKNOWN"),maximum=8),
                     "provider_qc_applied":qc_applied,"provider_qc_results":qc_results,
                     "provider_checks_without_failures":qc_applied>0 and qc_results==0,
                     "provider_qc_code":qc_code,"provider_trust_factor":trust,
                     "provider_data_source":identity(row["dataSource"],maximum=16),
                     "local_qc_certified":False,"station_representativeness_certified":False,
                     "settlement_authority":False,"calibration_label_authority":False,
                     "financial_authority":False,"source_role":"PWS_AUXILIARY_ONLY"}
            value["observation_key"] = digest({"provider":"PWSWEATHER","station":station,"observed_at":observed})
            value["observation_identity"] = digest({k:v for k,v in value.items()
                                                   if k not in {"local_received_at","age_at_receipt_seconds"}})
            observations.append(value)
        except (EvidenceError, KeyError, ValueError, TypeError, OverflowError) as exc:
            code = str(exc) if isinstance(exc, EvidenceError) else "XWEATHER_RECORD_SCHEMA_INVALID"
            rejected[code] += 1
    return {"adapter_version":XWEATHER_ADAPTER_VERSION, "observations":observations,
            "rejections":dict(rejected),"response_records":len(rows),
            "financial_authority":False,"settlement_authority":False,
            "lead_advantage_verified":False,"complete_geographic_coverage":False}


def normalize_weather_capture(store: EvidenceStore, raw_id: str, *, record_id: str,
                              station: str, official_max_age_seconds: float = 3600) -> dict:
    raw = store.get(raw_id)
    body,payload = raw["body"],raw["body"]["payload"]
    request_sha = digest(dict(raw_id=raw_id, raw_sha256=raw['sha256'], station=station,
                              official_max_age_seconds=official_max_age_seconds))
    try:
        prior = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    else:
        if (prior['kind'] != raw['kind'] or prior['event_id'] != raw['event_id']
                or prior['body'].get('payload', {}).get('normalization_sha256') != request_sha):
            raise EvidenceError('WEATHER_NORMALIZATION_ID_CONFLICT')
        return prior
    head = store.latest(kind=raw['kind'], event_id=raw['event_id'])
    current = store.latest_source(kind=raw['kind'], event_id=raw['event_id'],
                                  provider=body['provider'], source_identity=body['source_identity'])
    if current['id'] != raw_id:
        raise EvidenceError('WEATHER_RAW_SUPERSEDED')
    if body["evidence_class"] == "HISTORICAL_AVAILABILITY_UNKNOWN":
        raise EvidenceError("HISTORICAL_RESPONSE_NOT_CAUSAL")
    if body["provider"] == "NOAA_MADIS_CWOP" and raw["kind"] == "PWS_OBSERVATION":
        normalized = parse_madis_xml(payload["response"],received_at=body["received_at"],params=payload["request_params"])
    elif body["provider"] == "NOAA_AWC" and raw["kind"] == "OFFICIAL_OBSERVATION":
        normalized = parse_awc_metar(payload["response"],station=station,received_at=body["received_at"],
                                     max_age_seconds=official_max_age_seconds)
    else:
        raise EvidenceError("WEATHER_NORMALIZER_NOT_REVIEWED")
    now = finite(store.clock())
    if now < body['received_at']:
        raise EvidenceError('WEATHER_RECEIPT_IN_FUTURE')
    normalized.update(raw_evidence_id=raw_id,raw_evidence_sha256=raw["sha256"],
                      normalization_sha256=request_sha, feature_ready_at=now,settlement_station_context=station,
                      observed_time_scope='LATEST_ACCEPTED_PROVIDER_OBSERVATION')
    observed = max((finite(x['observed_at']) for x in normalized['observations']), default=None)
    derived = dict(provider=body['provider'], source_identity=body['source_identity'], revision=body['revision'],
                   payload=normalized, evidence_class=body['evidence_class'], source_kind=raw['kind'],
                   observed_at=observed, issued_at=None, published_at=None, received_at=body['received_at'])
    return store._append(record_id, raw['kind'], raw['event_id'], derived, now, now,
                         expected_previous_seq=head['seq'] if head else 0)
