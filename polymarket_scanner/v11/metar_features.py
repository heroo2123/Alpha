"""Optional physical context from documented METAR body tokens.

Units come from the tokens themselves. Remarks do not silently replace body
values, and a decoded report remains a proxy, not the contract population.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .evidence import finite


def parse_metar_physical(raw, *, station: str, observed_at: float) -> dict:
    result={'version':'alpha_v11_metar_physical_v1','status':'UNAVAILABLE','reasons':[],
            'wind_speed_mps':None,'wind_gust_mps':None,'wind_direction_degrees':None,'wind_variable':None,
            'body_temperature_c':None,'body_dewpoint_c':None,'cloud_layers':None,'vertical_visibility_m':None,
            'reported_weather_codes':None,'settlement_authority':False,'financial_authority':False}
    if not isinstance(raw,str) or not 1<=len(raw)<=4096:
        result['reasons'].append('METAR_BODY_MISSING_OR_OVERSIZED')
        return result
    tokens=raw.strip().rstrip('=').split()
    if tokens and tokens[0] in {'METAR','SPECI'}:
        tokens=tokens[1:]
    if len(tokens)<2 or tokens[0]!=station or tokens[1]!=datetime.fromtimestamp(finite(observed_at),timezone.utc).strftime('%d%H%MZ'):
        result['reasons'].append('METAR_STATION_OR_OBSERVATION_TIME_MISMATCH')
        return result
    tokens=tokens[2:tokens.index('RMK')] if 'RMK' in tokens else tokens[2:]
    if len(tokens)>128 or 'NIL' in tokens:
        result['reasons'].append('METAR_NIL_OR_TOKEN_BOUND')
        return result
    result['status']='PARSED_AUXILIARY'
    winds=[re.fullmatch(r'(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS|KMH)',s) for s in tokens]
    winds=[m for m in winds if m]
    if len(winds)==1:
        direction,speed,gust,unit=winds[0].groups()
        factor={'KT':1852/3600,'MPS':1.,'KMH':1/3.6}[unit]
        speed=float(speed)*factor
        gust=None if gust is None else float(gust)*factor
        if (direction!='VRB' and int(direction)>360) or speed>150 or gust is not None and gust>150:
            result['reasons'].append('METAR_WIND_BOUND')
        else:
            result.update(wind_speed_mps=speed,wind_gust_mps=gust,wind_variable=direction=='VRB',
                          wind_direction_degrees=None if direction=='VRB' or speed==0 else float(direction)%360)
    elif len(winds)>1:
        result['reasons'].append('METAR_AMBIGUOUS_WIND')
    pairs=[re.fullmatch(r'(M?\d{2})/(M?\d{2})',s) for s in tokens]
    pairs=[m for m in pairs if m]
    if len(pairs)==1:
        def temperature(s): return -float(s[1:]) if s.startswith('M') else float(s)
        temp,dew=map(temperature,pairs[0].groups())
        if not -90<=dew<=temp<=60:
            result['reasons'].append('METAR_BODY_TEMPERATURE_BOUND')
        else:
            result.update(body_temperature_c=temp,body_dewpoint_c=dew)
    elif len(pairs)>1:
        result['reasons'].append('METAR_AMBIGUOUS_TEMPERATURE')
    clouds=[]
    for token in tokens:
        m=re.fullmatch(r'(FEW|SCT|BKN|OVC)(\d{3}|///)(CB|TCU)?',token)
        if m:
            cover,base,kind=m.groups()
            clouds.append({'cover':cover,'base_m':None if base=='///' else int(base)*100*.3048,'kind':kind})
        elif token in {'CLR','SKC','NSC','NCD'}:
            clouds.append({'cover':token,'base_m':None,'kind':None})
        elif re.fullmatch(r'VV\d{3}',token):
            result['vertical_visibility_m']=int(token[2:])*100*.3048
    if clouds:
        if len(clouds)>8:
            result['reasons'].append('METAR_CLOUD_LAYER_BOUND')
        else:
            result['cloud_layers']=clouds
    weather=[s for s in tokens if s in {'TS','VCTS'} or re.fullmatch(r'(?:[-+]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS){1,3}',s)]
    result['reported_weather_codes']=weather[:3] if len(weather)<=3 else None
    if len(weather)>3:
        result['reasons'].append('METAR_WEATHER_GROUP_BOUND')
    return result
