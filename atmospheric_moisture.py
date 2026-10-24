"""Magnus water/ice moisture conversions; no transport or glider calculations."""
import math

from config import PRESSURE_LEVELS_HPA


def saturation_pressure(temperature_c, *, ice=False):
    if temperature_c is None or not math.isfinite(temperature_c) or not -100 <= temperature_c <= 65:
        return None
    a,b = (22.587,273.86) if ice else (17.625,243.04)
    return 6.112*math.exp(a*temperature_c/(b+temperature_c))


def relative_humidity(temperature_c, dewpoint_c, *, ice=False):
    """Dewpoint is referenced to water; ice RH may legitimately exceed 100%."""
    vapour = saturation_pressure(dewpoint_c)
    saturated = saturation_pressure(temperature_c,ice=ice)
    if vapour is None or saturated is None:return None
    result = 100*vapour/saturated
    return max(0,result) if ice else min(100,max(0,result))


def dewpoint(temperature_c, rh_percent):
    if saturation_pressure(temperature_c) is None or rh_percent is None or not math.isfinite(rh_percent) or not 0 < rh_percent <= 100:
        return None
    gamma = math.log(rh_percent/100)+17.625*temperature_c/(243.04+temperature_c)
    return 243.04*gamma/(17.625-gamma)


def ice_relative_humidity(temperature_c, rh_percent):
    water=saturation_pressure(temperature_c)
    ice=saturation_pressure(temperature_c,ice=True)
    if water is None or ice is None or rh_percent is None or not math.isfinite(rh_percent) or not 0<=rh_percent<=100:
        return None
    return rh_percent*water/ice


def complete_moisture(values, units, sources):
    """Fill RH from T/Td, or Td from T/RH; preserve all supplied values.

    Operates on a newly built forecast's dictionaries. No assumed saturation,
    cloud cover, or CAPE is assigned when measurements/model fields are absent.
    """
    for pressure in PRESSURE_LEVELS_HPA:
        tkey,dkey,rkey = (f'{n}_{pressure}hPa' for n in ('temperature','dew_point','relative_humidity'))
        if tkey not in values:continue
        n=len(values[tkey]);td=list(values.get(dkey,(None,)*n));rh=list(values.get(rkey,(None,)*n))
        dsrc=list(sources.get(dkey,(None,)*n));rsrc=list(sources.get(rkey,(None,)*n))
        for i,t in enumerate(values[tkey]):
            if rh[i] is None:
                estimate=relative_humidity(t,td[i])
                if estimate is not None:rh[i],rsrc[i]=estimate,'Magnus RH from temperature/dewpoint'
            if td[i] is None:
                estimate=dewpoint(t,rh[i])
                if estimate is not None:td[i],dsrc[i]=estimate,'Magnus dewpoint from temperature/RH'
        # Compact/subset requests must not grow extra arrays unless derivable.
        for key,series,origins,unit in ((dkey,td,dsrc,'°C'),(rkey,rh,rsrc,'%')):
            if key in values or any(origins):
                values[key]=tuple(series)
                if any(origins):sources[key]=tuple(origins);units[key]=unit
