"""Bounded public weather adapters preserving receipt time and source authority.

MADIS temperature units and XML fields follow the official Surface Viewer docs.
The public APRSWXNET subset is CWOP. Neither it nor AWC proxy observations are
accepted here as the exact contract's settlement population or finality source.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
import xml.etree.ElementTree as ET

from .evidence import EvidenceError, EvidenceStore, digest, finite, identity


MADIS_ENDPOINT = "https://madis-data.ncep.noaa.gov/madisPublic1/cgi-bin/madisXmlPublicDir"
MADIS_FIXED = {"time":"0", "minfwd":"0", "recwin":"3", "timefilter":"0", "dfltrsel":"1",
               "stasel":"0", "pvdrsel":"1", "pvd":"APRSWXNET", "varsel":"1", "nvars":"T",
               "qctype":"0", "qcsel":"1", "xml":"1", "csvmiss":"0"}


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


def normalize_weather_capture(store: EvidenceStore, raw_id: str, *, record_id: str,
                              station: str, official_max_age_seconds: float = 3600) -> dict:
    raw = store.get(raw_id)
    body,payload = raw["body"],raw["body"]["payload"]
    if body["evidence_class"] == "HISTORICAL_AVAILABILITY_UNKNOWN":
        raise EvidenceError("HISTORICAL_RESPONSE_NOT_CAUSAL")
    if body["provider"] == "NOAA_MADIS_CWOP" and raw["kind"] == "PWS_OBSERVATION":
        normalized = parse_madis_xml(payload["response"],received_at=body["received_at"],params=payload["request_params"])
    elif body["provider"] == "NOAA_AWC" and raw["kind"] == "OFFICIAL_OBSERVATION":
        normalized = parse_awc_metar(payload["response"],station=station,received_at=body["received_at"],
                                     max_age_seconds=official_max_age_seconds)
    else:
        raise EvidenceError("WEATHER_NORMALIZER_NOT_REVIEWED")
    normalized.update(raw_evidence_id=raw_id,raw_evidence_sha256=raw["sha256"],
                      feature_ready_at=store.clock(),settlement_station_context=station)
    return store.capture(record_id,event_id=raw["event_id"],kind=raw["kind"],provider=body["provider"],
                         source_identity=body["source_identity"],revision=body["revision"],payload=normalized,
                         evidence_class=body["evidence_class"])
