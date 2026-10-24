"""Fetch verified public Mapzen/Terrarium elevation tiles for UK ridge windows."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,UTC
from pathlib import Path
from urllib.request import Request,urlopen
import hashlib,io,json,math,sys
import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from terrain_wave import DOMAINS,TERRAIN_PATH,META_PATH,mercator_y,inverse_y

ZOOM=8
SOURCE='https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
CACHE=Path(__file__).resolve().parents[1]/'var'/'terrain-tiles'


def decode_terrarium(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.size!=(256,256) or image.mode not in ('RGB','RGBA'):raise ValueError('Invalid Terrarium tile')
        rgb=np.asarray(image,dtype=np.float64)
    return rgb[:,:,0]*256+rgb[:,:,1]+rgb[:,:,2]/256-32768


def tile(key):
    x,y=key;path=CACHE/f'{ZOOM}-{x}-{y}.png'
    if path.exists():raw=path.read_bytes()
    else:
        with urlopen(Request(SOURCE.format(z=ZOOM,x=x,y=y),headers={'User-Agent':'DuoDiscusWeather/1.0 (terrain wave modelling)'}),timeout=25) as r:raw=r.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Oversize DEM tile')
        decode_terrarium(raw);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return key,decode_terrarium(raw),hashlib.sha256(raw).hexdigest()


def grids():
    result=[];n=2**ZOOM*256
    for key,name,lat,lon in DOMAINS:
        cos=math.cos(math.radians(lat));offset=(np.arange(256)-127.5)*500/(6378137*cos)
        xs=math.radians(lon)+offset;ys=mercator_y(lat)+offset
        px=(xs+math.pi)/(2*math.pi)*n-.5;py=(math.pi-ys)/(2*math.pi)*n-.5
        xx,yy=np.meshgrid(px,py)
        bounds=[[inverse_y(ys[0]-offset[1]/2+offset[0]/2),math.degrees(xs[0]-(offset[1]-offset[0])/2)],
                [inverse_y(ys[-1]+(offset[1]-offset[0])/2),math.degrees(xs[-1]+(offset[1]-offset[0])/2)]]
        result.append(({'key':key,'name':name,'latitude':lat,'longitude':lon,'spacing_m':500,'bounds':bounds},xx,yy))
    return result


def main():
    samples=grids();keys=set()
    for _,xx,yy in samples:
        keys.update((int(x),int(y)) for x,y in zip(np.floor(xx/256).ravel(),np.floor(yy/256).ravel()))
    # Fail the preflight before starting a bulk fetch when access is unavailable.
    first=tile(sorted(keys)[0]);tiles={first[0]:first[1]};hashes={str(first[0]):first[2]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key,array,digest in pool.map(tile,[key for key in sorted(keys) if key!=first[0]]):tiles[key]=array;hashes[str(key)]=digest
    arrays={};metadata=[]
    for entry,xx,yy in samples:
        heights=np.zeros(xx.shape)
        for key,array in tiles.items():
            mask=(np.floor(xx/256)==key[0])&(np.floor(yy/256)==key[1])
            if mask.any():heights[mask]=map_coordinates(array,[yy[mask]-key[1]*256,xx[mask]-key[0]*256],order=1,mode='nearest')
        heights=np.maximum(0,heights)
        if heights.max()<200 or heights.max()>1600:raise ValueError('Unexpected UK ridge heights: '+entry['name'])
        arrays[entry['key']]=np.rint(heights).astype(np.int16);metadata.append(entry)
    TERRAIN_PATH.parent.mkdir(exist_ok=True);temporary=TERRAIN_PATH.with_suffix('.tmp')
    with temporary.open('wb') as stream:np.savez_compressed(stream,**arrays)
    raw=temporary.read_bytes();meta={'schema':1,'source':SOURCE,'zoom':ZOOM,'retrieved_at':datetime.now(UTC).isoformat(),
        'sha256':hashlib.sha256(raw).hexdigest(),'tile_sha256':hashes,'domains':metadata,
        'attribution':'Mapzen elevation tiles / AWS Open Data; SRTM, GMTED and other public elevation sources. https://registry.opendata.aws/terrain-tiles/',
        'processing':'Bilinear sampling at 500 m spacing; ocean/bathymetric negative heights set to sea level. No synthetic ridge terrain.'}
    temporary.replace(TERRAIN_PATH);META_PATH.write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({'domains':len(arrays),'tiles':len(tiles),'bytes':len(raw),'sha256':meta['sha256']}))


if __name__=='__main__':main()
