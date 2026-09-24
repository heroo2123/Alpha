"""Small, strict GRIB2 temperature subsets; no native decoder or network.

Only regular 0.5-degree GEFS point-temperature fields with simple/IEEE packing
are supported. Unsupported packing, missing cells or extra fields fail closed.
This is a forecast grid, never an observation or a settlement label.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import struct

from .evidence import EvidenceError


VERSION = 'alpha_v11_gefs_grib_subset_v1'
MAX_BYTES = 64 * 1024
MAX_POINTS = 25


def _uint(data, start, end):
    return int.from_bytes(data[start:end], 'big')


def _signed(data, start, end):
    n = _uint(data, start, end); sign = 1 << (8 * (end-start)-1)
    return -(n & (sign-1)) if n & sign else n


def _sections(data):
    if (type(data) is not bytes or not 16 <= len(data) <= MAX_BYTES
            or data[:4] != b'GRIB' or data[4:8] != b'\x00\x00\x00\x02'
            or _uint(data, 8, 16) != len(data) or data[-4:] != b'7777'):
        raise EvidenceError('GRIB_ENVELOPE_OR_BYTES_BOUND')
    out = {}; offset = 16; order = []
    while offset < len(data)-4:
        if offset+5 > len(data)-4: raise EvidenceError('GRIB_SECTION_TRUNCATED')
        length = _uint(data, offset, offset+4); number = data[offset+4]
        if length < 5 or offset+length > len(data)-4 or number in out:
            raise EvidenceError('GRIB_SECTION_LENGTH_OR_DUPLICATE')
        out[number] = data[offset:offset+length]; order.append(number); offset += length
        if len(order) > 7: raise EvidenceError('GRIB_SINGLE_FIELD_REQUIRED')
    if offset != len(data)-4 or order not in ([1,3,4,5,6,7], [1,2,3,4,5,6,7]):
        raise EvidenceError('GRIB_SINGLE_FIELD_SECTION_ORDER')
    if 2 in out and len(out[2]) > 1024: raise EvidenceError('GRIB_LOCAL_SECTION_BOUND')
    return out


@dataclass(frozen=True)
class GEFSField:
    initialized_at: float
    forecast_hour: int
    member: int
    declared_ensemble_size: int
    grid: tuple[tuple[float, float], ...]
    kelvin: tuple[float, ...]
    packing_template: int

    @property
    def valid_at(self): return self.initialized_at + self.forecast_hour * 3600


def decode_gefs_field(data):
    """Decode exactly one bounded NCEP operational 2m TMP ensemble field."""
    s = _sections(data); ident, grid, product, packing = (s[n] for n in (1,3,4,5))
    if (len(ident) != 21 or _uint(ident,5,7) != 7 or _uint(ident,7,9) != 0
            or not 2 <= ident[9] <= 35 or ident[11] != 1 or ident[19] != 0):
        raise EvidenceError('GRIB_OPERATIONAL_NCEP_INITIALIZATION_REQUIRED')
    try:
        run = datetime(_uint(ident,12,14), *ident[14:19], tzinfo=timezone.utc)
    except (ValueError, TypeError): raise EvidenceError('GRIB_INITIALIZATION_INVALID') from None
    if run.hour not in (0,6,12,18) or run.minute or run.second:
        raise EvidenceError('GRIB_GEFS_CYCLE_INVALID')
    if (len(product) != 37 or _uint(product,5,7) != 0 or _uint(product,7,9) != 1
            or product[9:12] != b'\x00\x00\x04' or product[13] != 107
            or product[17] != 1 or product[22] != 103 or product[23] != 0
            or _uint(product,24,28) != 2 or product[28] != 255):
        raise EvidenceError('GRIB_GEFS_POINT_TEMPERATURE_REQUIRED')
    hour = _uint(product,18,22); member = product[35]
    if (not 0 <= hour <= 240 or hour % 3 or not 0 <= member <= 30
            or product[36] not in (30,31)
            or (member == 0 and (product[34] != 1 or ident[20] != 3))
            or (member > 0 and (product[34] != 3 or ident[20] != 4))):
        raise EvidenceError('GRIB_MEMBER_OR_FORECAST_HOUR_INVALID')
    # Validate dimensions and byte counts before allocating arrays. No global
    # grid, quasi-regular grid, bitmap or alternative scan order is inferred.
    if (len(grid) != 72 or grid[5] != 0 or grid[10:14] != b'\x00\x00\x00\x00'
            or grid[14] != 6 or _uint(grid,38,42) != 0
            or _uint(grid,42,46) != 0xffffffff or grid[71] not in (0,64,128,192)):
        raise EvidenceError('GRIB_SMALL_REGULAR_GRID_REQUIRED')
    ni, nj, count = _uint(grid,30,34), _uint(grid,34,38), _uint(grid,6,10)
    if not 1 <= ni <= 5 or not 1 <= nj <= 5 or ni*nj != count or not 1 <= count <= MAX_POINTS:
        raise EvidenceError('GRIB_GRID_POINT_BOUND')
    if _uint(grid,63,67) != 500000 or _uint(grid,67,71) != 500000:
        raise EvidenceError('GRIB_GEFS_GRID_RESOLUTION')
    lat, lon = _signed(grid,46,50)/1e6, _signed(grid,50,54)/1e6
    last_lat, last_lon = _signed(grid,55,59)/1e6, _signed(grid,59,63)/1e6
    dx = -.5 if grid[71] & 128 else .5; dy = .5 if grid[71] & 64 else -.5
    if (not -90 <= lat <= 90 or not 0 <= lon <= 360 or not -90 <= last_lat <= 90
            or abs(lat+(nj-1)*dy-last_lat) > 1e-6
            or abs(((lon+(ni-1)*dx-last_lon+180)%360)-180) > 1e-6):
        raise EvidenceError('GRIB_GRID_ENDPOINT_MISMATCH')
    points = tuple((lat+j*dy, ((lon+i*dx+180)%360)-180) for j in range(nj) for i in range(ni))
    if len(s[6]) != 6 or s[6][5] != 255:
        raise EvidenceError('GRIB_MISSING_OR_BITMAP_UNSUPPORTED')
    if len(packing) < 12 or _uint(packing,5,9) != count:
        raise EvidenceError('GRIB_PACKED_POINT_COUNT')
    template = _uint(packing,9,11); payload = s[7][5:]
    if template == 0:
        if len(packing) != 21 or packing[20] != 0: raise EvidenceError('GRIB_SIMPLE_PACKING_SCHEMA')
        reference = struct.unpack('>f',packing[11:15])[0]
        binary, decimal, bits = _signed(packing,15,17), _signed(packing,17,19), packing[19]
        if (not math.isfinite(reference) or abs(binary) > 32 or abs(decimal) > 8
                or bits > 32 or len(payload) != (bits*count+7)//8):
            raise EvidenceError('GRIB_PACKED_VALUE_BOUND')
        packed = int.from_bytes(payload,'big'); padding = len(payload)*8-count*bits
        if padding and packed & ((1<<padding)-1): raise EvidenceError('GRIB_NONZERO_PADDING')
        values = tuple((reference+((packed >> (padding+(count-1-i)*bits)) & ((1<<bits)-1))*2.**binary)*10.**(-decimal)
                       for i in range(count))
    elif template == 4:
        if len(packing) != 12 or packing[11] not in (1,2): raise EvidenceError('GRIB_IEEE_PRECISION')
        width = 4 if packing[11] == 1 else 8
        if len(payload) != count*width: raise EvidenceError('GRIB_IEEE_LENGTH')
        values = struct.unpack('>'+('f' if width==4 else 'd')*count,payload)
    else:
        raise EvidenceError('GRIB_PACKING_DECODER_UNAVAILABLE')
    if any(not math.isfinite(v) or not 150 <= v <= 350 for v in values):
        raise EvidenceError('GRIB_TEMPERATURE_OR_MISSING_VALUE')
    return GEFSField(run.timestamp(),hour,member,product[36],points,values,template)
