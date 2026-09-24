"""Synthetic WMO-format byte fixtures, not actual NOAA access/parity evidence."""
from datetime import datetime, timezone
import struct

import pytest

from polymarket_scanner.v11.grib_fields import decode_gefs_field
from polymarket_scanner.v11.evidence import EvidenceError


RUN=datetime(2026,9,14,tzinfo=timezone.utc).timestamp()


def u(value,size):return int(value).to_bytes(size,'big')


def signed(value,size):return u((1<<(size*8-1))|(-int(value)) if value<0 else value,size)


def section(number,length):
    data=bytearray(length);data[:5]=u(length,4)+u(number,1);return data


def message(sections):
    body=b''.join(sections)+b'7777'
    return b'GRIB\0\0\0\x02'+u(16+len(body),8)+body


def grib(*,member=0,hour=3,run=RUN,values=(290.,291.,292.,293.),packing=0,lat=33.,lon=276.,scan=64):
    d=datetime.fromtimestamp(run,timezone.utc);a=section(1,21)
    a[5:12]=u(7,2)+u(0,2)+bytes([2,1,1]);a[12:19]=u(d.year,2)+bytes([d.month,d.day,d.hour,d.minute,d.second])
    a[19:21]=bytes([0,3 if member==0 else 4])
    b=section(3,72);b[6:10]=u(4,4);b[14]=6;b[30:38]=u(2,4)+u(2,4);b[42:46]=b'\xff'*4
    dx=-.5 if scan&128 else .5;dy=.5 if scan&64 else -.5
    b[46:54]=signed(round(lat*1e6),4)+signed(round(lon*1e6),4);b[54]=48
    b[55:63]=signed(round((lat+dy)*1e6),4)+signed(round((lon+dx)*1e6),4)
    b[63:72]=u(500000,4)+u(500000,4)+bytes([scan])
    c=section(4,37);c[7:14]=u(1,2)+bytes([0,0,4,0,107]);c[17]=1;c[18:22]=u(hour,4)
    c[22:29]=bytes([103,0])+u(2,4)+b'\xff';c[29:34]=b'\xff'*5
    c[34:37]=bytes([1 if member==0 else 3,member,31])
    if packing==0:
        p=section(5,21);p[5:11]=u(4,4)+u(0,2);p[11:15]=struct.pack('>f',280.)
        p[19]=8;packed=bytes(int(v-280.) for v in values)
    else:
        p=section(5,12);p[5:12]=u(4,4)+u(4,2)+bytes([packing]);packed=struct.pack('>'+('f' if packing==1 else 'd')*4,*values)
    bitmap=section(6,6);bitmap[5]=255;data=section(7,5+len(packed));data[5:]=packed
    return message([a,b,c,p,bitmap,data])


def mutate(data,number,offset,content):
    parts=[];pos=16
    while pos<len(data)-4:
        size=int.from_bytes(data[pos:pos+4],'big');part=bytearray(data[pos:pos+size])
        if part[4]==number:part[offset:offset+len(content)]=content
        parts.append(part);pos+=size
    return message(parts)


@pytest.mark.parametrize('packing',[0,1,2])
@pytest.mark.parametrize('member',[0,1,30])
def test_strict_decoder_preserves_run_member_grid_and_unrounded_temperature(packing,member):
    values=(290.,291.,292.,293.) if packing==0 else (290.125,291.25,292.5,293.75)
    field=decode_gefs_field(grib(member=member,packing=packing,values=values))
    assert field.initialized_at==RUN and field.valid_at==RUN+10800 and field.member==member
    assert field.grid==((33.,-84.),(33.,-83.5),(33.5,-84.),(33.5,-83.5)) and field.kelvin==values


@pytest.mark.parametrize('scan',[0,64,128,192])
def test_scan_directions_and_signed_southern_latitude(scan):
    field=decode_gefs_field(grib(lat=-33.,lon=350.,scan=scan))
    assert field.grid[0]==(-33.,-10.)
    assert field.grid[-1]==(-33.+(.5 if scan&64 else -.5),-10.+(-.5 if scan&128 else .5))


@pytest.mark.parametrize('section_id,offset,value,reason',[
    (1,5,u(98,2),'OPERATIONAL_NCEP'),(1,11,b'\x00','OPERATIONAL_NCEP'),(1,19,b'\x01','OPERATIONAL_NCEP'),
    (4,7,u(11,2),'POINT_TEMPERATURE'),(4,10,b'\x04','POINT_TEMPERATURE'),(4,13,b'\x60','POINT_TEMPERATURE'),
    (4,24,u(10,4),'POINT_TEMPERATURE'),(4,35,b'\x1f','MEMBER_OR_FORECAST'),
    (4,18,u(4,4),'MEMBER_OR_FORECAST'),(3,30,u(1000000,4),'POINT_BOUND'),
    (3,63,u(250000,4),'GRID_RESOLUTION'),(3,71,b'\x20','SMALL_REGULAR_GRID'),
    (3,55,signed(35000000,4),'ENDPOINT_MISMATCH'),(6,5,b'\x00','BITMAP_UNSUPPORTED'),
    (5,9,u(3,2),'DECODER_UNAVAILABLE'),(5,19,b'\xff','PACKED_VALUE_BOUND'),
])
def test_other_products_missing_data_and_allocation_inflation_fail_closed(section_id,offset,value,reason):
    with pytest.raises(EvidenceError,match=reason):decode_gefs_field(mutate(grib(),section_id,offset,value))


@pytest.mark.parametrize('fault',['truncated','multiple','html','oversized','nan'])
def test_invalid_envelope_extra_fields_and_nonfinite_values_are_rejected(fault):
    data=grib()
    if fault=='truncated':data=data[:-1]
    elif fault=='multiple':data+=data
    elif fault=='html':data=b'<html>temporary response</html>'
    elif fault=='oversized':data=b'GRIB'+b'0'*65536+b'7777'
    else:data=grib(packing=2,values=(float('nan'),290.,290.,290.))
    with pytest.raises(EvidenceError):decode_gefs_field(data)


def test_simple_signed_scaling_constant_fields_and_padding():
    data=mutate(grib(),5,15,signed(-1,2)+signed(0,2))
    assert decode_gefs_field(data).kelvin==(285.,285.5,286.,286.5)
    # Constant packed fields have no payload and still have the full grid.
    data=grib();parts=[];pos=16
    while pos<len(data)-4:
        length=int.from_bytes(data[pos:pos+4],'big');p=bytearray(data[pos:pos+length]);pos+=length
        if p[4]==5:p[19]=0
        if p[4]==7:p=section(7,5)
        parts.append(p)
    assert decode_gefs_field(message(parts)).kelvin==(280.,)*4
