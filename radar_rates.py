"""Optional calibrated radar grid. Image colours are never inverted into rain rates.

Contract: JSON schema_version=1, units='mm/h', observed_at=ISO UTC, source,
bounds=[[south,west],[north,east]], values=south-to-north rows of numeric rates
or null. A numeric zero is a dry observation; null means no coverage.
"""
from datetime import datetime, UTC, timedelta
import base64
import io
import math
from urllib.parse import urlsplit

import numpy as np
from PIL import Image
from folium.utilities import mercator_transform
from data_ingestion import JsonHttpClient, IngestionError, utc_datetime

STOPS=((0,'#2867bb'),(.6,'#35d1d5'),(3,'#39ae55'),(12,'#f5e84a'),(50,'#f88c30'),(100,'#ed3239'),(200,'#c836d8'))


def validate_grid(body,now=None):
    if not isinstance(body,dict) or body.get('schema_version')!=1 or body.get('units')!='mm/h':
        raise IngestionError('Radar grid must declare schema_version 1 and mm/h units')
    try:
        observed=utc_datetime(datetime.fromisoformat(body['observed_at'].replace('Z','+00:00')))
        (south,west),(north,east)=body['bounds']
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (south,west,north,east)):raise ValueError()
        if not -85<=south<north<=85 or not -180<=west<east<=180:raise ValueError()
        rows=body['values'];width=len(rows[0])
        if not 2<=len(rows)<=512 or not 2<=width<=512:raise ValueError()
        if any(len(row)!=width for row in rows):raise ValueError()
        for row in rows:
            for v in row:
                if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=2000):raise ValueError()
        if observed>(now or datetime.now(UTC))+timedelta(minutes=10):raise ValueError()
        source=body['source']
        if not isinstance(source,str) or not source.strip() or len(source)>300:raise ValueError()
    except (ValueError,TypeError,KeyError,IndexError) as exc:
        raise IngestionError('Invalid radar grid geometry, time, rates or source') from exc
    return {key:body[key] for key in ('schema_version','units','observed_at','source','bounds','values')}


def load_rate_grid(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IngestionError('Calibrated radar URL must be credential-free HTTPS without query or fragment')
    return validate_grid(JsonHttpClient().get_json(url,{},ttl_seconds=300).body)


def rate_at(grid,latitude,longitude):
    (south,west),(north,east)=grid['bounds']
    if not south<=latitude<=north or not west<=longitude<=east:return None
    rows=grid['values'];y=min(len(rows)-1,int((latitude-south)/(north-south)*len(rows)))
    x=min(len(rows[0])-1,int((longitude-west)/(east-west)*len(rows[0])))
    return rows[y][x]


def rate_overlay(grid):
    values=np.array(grid['values'],dtype=float)[::-1]
    positions=np.array([p for p,_ in STOPS]);colours=np.array([[int(c[i:i+2],16) for i in (1,3,5)] for _,c in STOPS])
    image=np.zeros((*values.shape,4),dtype=np.uint8)
    for channel in range(3):image[...,channel]=np.interp(np.nan_to_num(values),positions,colours[:,channel]).round().astype('uint8')
    image[...,3]=np.where(np.isfinite(values)&(values>0),205,0)
    image=np.rint(mercator_transform(image,(grid['bounds'][0][0],grid['bounds'][1][0]),origin='upper')).astype('uint8')
    out=io.BytesIO();Image.fromarray(image).save(out,format='PNG')
    return {**grid,'image':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()}
