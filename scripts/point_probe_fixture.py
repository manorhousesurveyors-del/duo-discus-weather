"""Generate a clearly synthetic, varying two-day chart fixture (never live data)."""
from datetime import UTC,datetime
from pathlib import Path
import math
from alerts import ForecastBundle,save_bundle
from config import forecast_locations
from tests.fixtures import forecast


def write_fixture(path):
    start=datetime(2026,10,24,tzinfo=UTC);hours=144
    sun=[max(0,math.sin(math.pi*((i%24)-6)/12)) for i in range(hours)]
    wave=[math.sin(i/12) for i in range(hours)]
    values={'temperature_2m':[10+14*s for s in sun],'dew_point_2m':[8+2*s for s in sun],
        'shortwave_radiation':[950*s for s in sun],'direct_radiation':[800*s for s in sun],
        'boundary_layer_height':[100+2100*s for s in sun],
        'soil_moisture_0_to_7cm':.08,'wind_gusts_10m':[12+10*s for s in sun],
        'wind_speed_10m':[6+4*s for s in sun],'wind_direction_10m':[260+20*w for w in wave],
        'cloud_cover_low':[20+40*s for s in sun],'cloud_cover_mid':35,'cloud_cover_high':50,
        'pressure_msl':[1006+i*.04+3*w for i,w in enumerate(wave)],
        'geopotential_height_250hPa':10400.,'temperature_250hPa':-50.,
        'relative_humidity_250hPa':[80+15*w for w in wave],
        'wind_speed_250hPa':35.,'wind_direction_250hPa':260.,
        'geopotential_height_200hPa':12000.,'temperature_200hPa':-56.,
        'relative_humidity_200hPa':50.,'wind_speed_200hPa':40.,'wind_direction_200hPa':270.}
    forecasts=tuple(forecast(p,start=start,hours=hours,changes=values) for p in forecast_locations())
    path.parent.mkdir(parents=True,exist_ok=True)
    save_bundle(ForecastBundle(forecasts,(),.5,('Synthetic varying weather for probe validation.',),start),path)


if __name__=='__main__':write_fixture(Path('var/point-probe-snapshot.json'))
