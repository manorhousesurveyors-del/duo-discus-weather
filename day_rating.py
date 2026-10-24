"""UK RASP-style distance bands with explicit, independent quality caps.

Thresholds follow the requested distance categories, not RASP's proprietary
index. This is a forecast screening heuristic, not validation against flights.
"""
from dataclasses import dataclass
from datetime import datetime,time
from statistics import median
import math
from config import LOCAL_TIMEZONE,METRES_TO_FEET
from soaring_physics import Profile,evaluate_thermals,select_forecast
from task_planning import classify_day,FORECAST_PREFERENCE

LABELS=('Very Poor / Poor','Reasonable','Getting Good','Good','Optimum Star Day')


@dataclass(frozen=True)
class DayRating:
    day: str
    scope: str
    stars: int | None
    distance_km: float | None
    climb_knots: float | None
    base_agl_ft: float | None
    window_hours: float | None
    wave: bool
    reasons: tuple[str,...]

    @property
    def symbols(self):return 'Unavailable' if self.stars is None else '★'*self.stars+'☆'*(5-self.stars)

    @property
    def label(self):return 'Insufficient forecast coverage' if self.stars is None else LABELS[self.stars-1]


def calibrated_stars(pfd,climb,base_agl,window,overdevelopment=0):
    if any(v is None or not math.isfinite(v) for v in (pfd,climb,base_agl,window,overdevelopment)):return None,('Required forecast fields are missing.',)
    stars=1+sum(pfd>=cut for cut in (50,180,350,500));reasons=[]
    for minimum,cap,actual,name in ((1.5,2,climb,'net climb'),(2.5,3,climb,'net climb'),(3.5,4,climb,'net climb'),
        (1800,2,base_agl,'usable height AGL'),(3000,3,base_agl,'usable height AGL'),(4500,4,base_agl,'usable height AGL'),
        (3,2,window,'soarable hours'),(4.5,3,window,'soarable hours'),(6,4,window,'soarable hours')):
        if actual<minimum and stars>cap:stars=cap;reasons.append(f'Limited by {name}.')
    if overdevelopment>=.5 and stars>2:stars=2;reasons.append('Spread-out risk affects at least half the window.')
    elif overdevelopment>=.25 and stars>3:stars=3;reasons.append('Spread-out risk reduces the rating.')
    return stars,tuple(dict.fromkeys(reasons))


def rate_day(forecasts,origin,day,loading='dry',*,preference=FORECAST_PREFERENCE,wave=False):
    forecasts=tuple(forecasts);classification=classify_day(forecasts,origin,day,loading,preference=preference,wave_available=wave)
    climbs=[];bases=[];risks=[];run=longest=0;known=0
    for hour in range(10,18):
        when=datetime.combine(day,time(hour),LOCAL_TIMEZONE)
        f=select_forecast(forecasts,origin.key,when,preference)
        if f is None:run=0;continue
        p=Profile.from_forecast(f,when);t=evaluate_thermals(p,loading)
        if (t.conservative_climb_knots is None or t.usable_top_ft_amsl is None
            or t.overdevelopment is None or p.values.get('precipitation') is None):run=0;continue
        known+=1;depth=t.usable_top_ft_amsl-f.terrain_elevation_m*METRES_TO_FEET
        usable=t.conservative_climb_knots>=1 and depth>=500*METRES_TO_FEET and p.values['precipitation']<1
        run=run+1 if usable else 0;longest=max(longest,run)
        if usable:climbs.append(t.conservative_climb_knots);bases.append(depth);risks.append(t.overdevelopment)
    climb=median(climbs) if climbs else 0.;base=median(bases) if bases else 0.
    stars,reasons=calibrated_stars(classification.distance_km,climb,base,longest,sum(risks)/len(risks) if risks else 0)
    if known<8:stars=None;reasons=(f'Only {known}/8 hours include climb, height, rain and spread-out evidence.',)
    if wave:reasons+=('Wave potential is separate; it does not raise the thermal XC rating.',)
    return DayRating(day.isoformat(),origin.name,stars,classification.distance_km,climb if known else None,
        base if known else None,longest if known else None,wave,reasons)


def best_rating(ratings):return max((r for r in ratings if r.stars is not None),key=lambda r:(r.stars,r.distance_km or 0),default=None)
