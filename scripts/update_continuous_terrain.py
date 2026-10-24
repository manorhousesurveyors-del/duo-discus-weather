"""Build one verified, seamless UK/Ireland DEM; no per-site crop windows."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,UTC
from pathlib import Path
import hashlib,json,math,sys
import numpy as np
from scipy.ndimage import map_coordinates
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.update_wave_terrain import tile,SOURCE,ZOOM
from terrain_wave import mercator_y

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'data'/'uk_continuous_terrain.npz'

def main():
    # Uniform Web-Mercator pixels, nominal 2 km ground spacing at 54.5 N.
    south,west,north,east=50.,-8.,59.,2.
    unit=6378137*math.cos(math.radians(54.5));dx=math.radians(east-west)*unit
    dy=(mercator_y(north)-mercator_y(south))*unit
    cols=math.ceil(dx/2000/2)*2;rows=math.ceil(dy/2000/2)*2
    xs=np.linspace(math.radians(west),math.radians(east),cols,endpoint=False)+math.radians(east-west)/cols/2
    ys=np.linspace(mercator_y(south),mercator_y(north),rows,endpoint=False)+(mercator_y(north)-mercator_y(south))/rows/2
    n=2**ZOOM*256;xx,yy=np.meshgrid((xs+math.pi)/(2*math.pi)*n-.5,(math.pi-ys)/(2*math.pi)*n-.5)
    keys=sorted(set(zip(np.floor(xx/256).astype(int).ravel(),np.floor(yy/256).astype(int).ravel())))
    tiles={};hashes={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key,array,digest in pool.map(tile,keys):tiles[key]=array;hashes[str(key)]=digest
    # A joined tile mosaic allows interpolation across tile boundaries.
    x0=min(k[0] for k in keys);y0=min(k[1] for k in keys)
    mosaic=np.zeros(((max(k[1] for k in keys)-y0+1)*256,(max(k[0] for k in keys)-x0+1)*256))
    for (x,y),a in tiles.items():mosaic[(y-y0)*256:(y-y0+1)*256,(x-x0)*256:(x-x0+1)*256]=a
    heights=np.maximum(0,map_coordinates(mosaic,[yy-y0*256,xx-x0*256],order=1,mode='nearest'))
    if not np.isfinite(heights).all() or not 1100<heights.max()<1500:raise ValueError('Invalid nationwide elevation coverage')
    tmp=PATH.with_suffix('.tmp')
    with tmp.open('wb') as f:np.savez_compressed(f,heights=np.rint(heights).astype(np.int16))
    digest=hashlib.sha256(tmp.read_bytes()).hexdigest();tmp.replace(PATH)
    meta={'schema':1,'bounds':[[south,west],[north,east]],'rows':rows,'cols':cols,'spacing_m':(dx/cols+dy/rows)/2,
        'projection':'Web Mercator; nominal ground metres at 54.5 N; scale varies across domain',
        'source':SOURCE,'zoom':ZOOM,'retrieved_at':datetime.now(UTC).isoformat(),'sha256':digest,'tile_sha256':hashes,
        'attribution':'Mapzen / AWS Open Data elevation; SRTM, GMTED and other public DEM sources',
        'processing':'Continuous bilinear tile mosaic, approximately 2 km sampling; negative heights set to sea level. Full requested 50–59 N, -8–2 E domain, including eastern Ireland.'}
    PATH.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({k:meta[k] for k in ('rows','cols','spacing_m','sha256')}))

if __name__=='__main__':main()
