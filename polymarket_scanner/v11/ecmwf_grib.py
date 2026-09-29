"""Bounded regular-grid ECMWF GRIB2 station extraction.

Simple and IEEE packing are decoded locally. CCSDS template 5.42 is decoded
through ECMWF ecCodes when that reviewed optional dependency is available;
unsupported packing and decoder failures remain explicit fail-closed gates.
CCSDS fields must exactly survive a bounded decode/re-encode integrity round-trip
before the selected station value is returned.
"""
from datetime import datetime, timezone
import hashlib
import math
import struct

from .evidence import EvidenceError, digest
from .grib_fields import _uint as uint, _signed as signed
from .model_panel import MAX_RAW_BYTES, temperature
from .pws_quality import geometry

VERSION = 'alpha_v11_ecmwf_station_grib_v2'


def _ccsds_value(data, index, count, ni, nj):
    """Decode one independently selected value from GRIB2 template 5.42."""
    if type(index) is not int or not 0 <= index < count:
        raise EvidenceError('ECMWF_CCSDS_INDEX_BOUND')
    try:
        import eccodes
    except Exception:
        # The Python package can exist while its native ecCodes library is absent.
        raise EvidenceError('ECMWF_CCSDS_DECODER_UNAVAILABLE') from None
    handle = None
    try:
        handle = eccodes.codes_new_from_message(data)
        if handle is None:
            raise EvidenceError('ECMWF_CCSDS_DECODE_FAILED')
        expected = {
            'edition': 2,
            'dataRepresentationTemplateNumber': 42,
            'numberOfDataPoints': count,
            'numberOfValues': count,
            'Ni': ni,
            'Nj': nj,
            'bitmapPresent': 0,
        }
        for key, value in expected.items():
            if int(eccodes.codes_get_long(handle, key)) != value:
                raise EvidenceError('ECMWF_CCSDS_DECODER_METADATA_MISMATCH')
        if eccodes.codes_get(handle, 'packingType', str) != 'grid_ccsds':
            raise EvidenceError('ECMWF_CCSDS_DECODER_METADATA_MISMATCH')
        # ecCodes can decode a self-consistent but truncated CCSDS section by
        # synthesizing plausible-looking values. Force a bounded full decode and
        # deterministic re-encode; the exact GRIB message must round-trip byte for
        # byte before any selected value is trusted.
        values = eccodes.codes_get_values(handle)
        if len(values) != count or any(not math.isfinite(float(v)) for v in values):
            raise EvidenceError('ECMWF_CCSDS_DECODE_FAILED')
        clone = eccodes.codes_clone(handle)
        try:
            eccodes.codes_set_values(clone, values)
            if eccodes.codes_get_message(clone) != data:
                raise EvidenceError('ECMWF_CCSDS_INTEGRITY_FAILED')
        finally:
            eccodes.codes_release(clone)
        return float(values[index])
    except EvidenceError:
        raise
    except Exception as exc:
        raise EvidenceError('ECMWF_CCSDS_DECODE_FAILED') from exc
    finally:
        if handle is not None:
            try:
                eccodes.codes_release(handle)
            except Exception:
                pass


def sections(data):
    if (type(data) is not bytes or not 16 <= len(data) <= MAX_RAW_BYTES
            or data[:4] != b'GRIB' or data[4:6] not in {b'\0\0', b'\xff\xff'}
            or data[6] != 0 or data[7] != 2
            or uint(data, 8, 16) != len(data) or data[-4:] != b'7777'):
        raise EvidenceError('ECMWF_GRIB_ENVELOPE')
    result = {}; offset = 16; order = []
    while offset < len(data)-4:
        if offset+5 > len(data)-4: raise EvidenceError('ECMWF_GRIB_TRUNCATED')
        length, number = uint(data, offset, offset+4), data[offset+4]
        if length < 5 or offset+length > len(data)-4 or number in result:
            raise EvidenceError('ECMWF_GRIB_SECTION_LENGTH')
        result[number] = data[offset:offset+length]; order.append(number); offset += length
        if len(order) > 7: raise EvidenceError('ECMWF_SINGLE_FIELD_REQUIRED')
    if order not in ([1, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7]):
        raise EvidenceError('ECMWF_SINGLE_FIELD_REQUIRED')
    if len(result.get(2, b'')) > 4096: raise EvidenceError('ECMWF_LOCAL_SECTION_BOUND')
    return result


def release_signature(data):
    """Observed GRIB header identity, to compare with a separately reviewed pin.

    This is not a vendor version attestation. Release evidence must also bind the
    operational model version and dataset; a hash alone cannot supply that fact.
    """
    s = sections(data)
    return digest(dict(identification=s[1][5:12].hex(), product=s[4][7:17].hex(),
                       local_section_sha256=hashlib.sha256(s.get(2, b'')).hexdigest()))


def decode_station(data, *, request, target):
    s = sections(data); ident, grid, product, packing = (s[n] for n in (1, 3, 4, 5))
    if release_signature(data) != request.grib_signature_sha256:
        raise EvidenceError('ECMWF_SOURCE_VERSION_CHANGED')
    if (len(ident) != 21 or uint(ident, 5, 7) != 98 or ident[11] != 1 or ident[19] != 0):
        raise EvidenceError('ECMWF_OPERATIONAL_RUN_REQUIRED')
    try:
        run = datetime(uint(ident, 12, 14), *ident[14:19], tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise EvidenceError('ECMWF_GRIB_TIME_INVALID') from None
    if len(product) not in (34, 37):
        raise EvidenceError('ECMWF_POINT_2T_REQUIRED')
    template = uint(product, 7, 9)
    if (uint(product, 5, 7) != 0 or product[9:11] != b'\0\0' or product[17] != 1
            or product[22:24] != bytes([103, 0]) or uint(product, 24, 28) != 2
            or product[28:34] != b'\xff'*6):
        raise EvidenceError('ECMWF_POINT_2T_REQUIRED')
    model = request.source.model
    if model == 'aifs-ens':
        valid_product = (len(product) == 37 and template == 1 and product[11] == 4
                         and product[34] == (5 if request.member == 0 else 6)
                         and product[35] == request.member and product[36] == 51
                         and ident[20] == 10)
    elif model == 'ifs' and request.member == 0:
        # IFS Cycle 50r1 publishes the former ensemble control as oper/fc.
        valid_product = (len(product) == 34 and template == 0 and product[11] == 2
                         and ident[20] == 1)
    elif model == 'ifs':
        valid_product = (len(product) == 37 and template == 1 and product[11] == 4
                         and product[34] == 255 and product[35] == request.member
                         and product[36] == 51 and ident[20] == 4)
    else:
        valid_product = False
    if (run != request.initialized_at or uint(product, 18, 22) != request.step
            or not valid_product):
        raise EvidenceError('ECMWF_RUN_MEMBER_STEP_MISMATCH')
    if (len(grid) != 72 or grid[5] != 0 or grid[10:14] != b'\0'*4
            or grid[14] not in (6, 8) or uint(grid, 38, 42) != 0
            or uint(grid, 42, 46) != 0xffffffff or grid[71] not in (0, 64, 128, 192)):
        raise EvidenceError('ECMWF_REGULAR_GRID_DECODER_UNAVAILABLE')
    ni, nj, count = uint(grid, 30, 34), uint(grid, 34, 38), uint(grid, 6, 10)
    if not 1 <= ni <= 1440 or not 1 <= nj <= 721 or count != ni*nj:
        raise EvidenceError('ECMWF_GRID_POINT_BOUND')
    if uint(grid, 63, 67) != 250000 or uint(grid, 67, 71) != 250000:
        raise EvidenceError('ECMWF_GRID_RESOLUTION_CHANGED')
    lat, lon = signed(grid, 46, 50)/1e6, signed(grid, 50, 54)/1e6
    last_lat, last_lon = signed(grid, 55, 59)/1e6, signed(grid, 59, 63)/1e6
    dx = -.25 if grid[71] & 128 else .25; dy = .25 if grid[71] & 64 else -.25
    if (not -90 <= lat <= 90 or not -90 <= last_lat <= 90 or not 0 <= lon <= 360
            or not 0 <= last_lon <= 360 or abs(lat+(nj-1)*dy-last_lat) > 1e-6
            or abs(((lon+(ni-1)*dx-last_lon+180)%360)-180) > 1e-6):
        raise EvidenceError('ECMWF_GRID_ENDPOINT_MISMATCH')
    if len(s[6]) != 6 or s[6][5] != 255:
        raise EvidenceError('ECMWF_BITMAP_DECODER_UNAVAILABLE')
    if len(packing) < 12 or uint(packing, 5, 9) != count:
        raise EvidenceError('ECMWF_PACKED_COUNT_MISMATCH')
    template = uint(packing, 9, 11); payload = s[7][5:]
    if template == 0:
        if len(packing) != 21 or packing[20] != 0: raise EvidenceError('ECMWF_SIMPLE_PACKING_SCHEMA')
        reference = struct.unpack('>f', packing[11:15])[0]
        binary, decimal, bits = signed(packing, 15, 17), signed(packing, 17, 19), packing[19]
        if (not math.isfinite(reference) or abs(binary) > 32 or abs(decimal) > 8 or bits > 32
                or len(payload) != (bits*count+7)//8):
            raise EvidenceError('ECMWF_PACKED_VALUE_BOUND')
        padding = len(payload)*8-count*bits
        if padding and payload[-1] & ((1 << padding)-1):
            raise EvidenceError('ECMWF_NONZERO_PADDING')
        def value(i):
            if not bits: return reference*10.**(-decimal)
            start, end = i*bits, (i+1)*bits
            word = int.from_bytes(payload[start//8:(end+7)//8], 'big')
            number = (word >> ((8-end % 8) % 8)) & ((1 << bits)-1)
            return (reference+number*2.**binary)*10.**(-decimal)
    elif template == 4:
        if len(packing) != 12 or packing[11] not in (1, 2): raise EvidenceError('ECMWF_IEEE_SCHEMA')
        width = 4 if packing[11] == 1 else 8
        if len(payload) != count*width: raise EvidenceError('ECMWF_IEEE_LENGTH')
        def value(i): return struct.unpack_from('>f' if width == 4 else '>d', payload, i*width)[0]
    elif template == 42:
        if len(packing) != 25:
            raise EvidenceError('ECMWF_CCSDS_PACKING_SCHEMA')
        def value(i): return _ccsds_value(data, i, count, ni, nj)
    else:
        raise EvidenceError('ECMWF_PACKING_DECODER_UNAVAILABLE')
    # At most 75 candidates, including wrap-around and endpoints. The grid must
    # be regular; the distance policy rejects any remote station substitution.
    j0 = round((target.latitude-lat)/dy)
    candidates = set()
    for shift in (-360, 0, 360):
        i0 = round((target.longitude+shift-lon)/dx)
        for i in (i0-1, i0, i0+1, 0, ni-1):
            for j in (j0-1, j0, j0+1, 0, nj-1):
                if 0 <= i < ni and 0 <= j < nj: candidates.add((i, j))
    def position(ij):
        i, j = ij
        return lat+j*dy, (lon+i*dx+180)%360-180
    selected = min(candidates, key=lambda ij: (geometry(target.latitude, target.longitude, *position(ij))[0], position(ij)))
    point = position(selected)
    if geometry(target.latitude, target.longitude, *point)[0] > target.maximum_grid_distance_km:
        raise EvidenceError('PANEL_GRID_DISTANCE_BOUND')
    kelvin = temperature(value(selected[1]*ni+selected[0]), 'K', 'K')
    return dict(latitude=point[0], longitude=point[1], kelvin=kelvin,
                grid_sha256=hashlib.sha256(grid).hexdigest())
