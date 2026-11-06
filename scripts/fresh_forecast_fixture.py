"""Isolated LIVE-path startup fixture: synthetic weather, no provider requests.

Run on port 8507 with an isolated DUO_TASK_STORE, then verify_fresh_forecast.py.
The deliberate two-second worker delay exercises the first pending render.
"""
import os
from pathlib import Path
import sys
from datetime import datetime
from time import sleep

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DUO_DASHBOARD_SNAPSHOT']=''
import app
import streamlit as st
import uk_map_grid
from data_ingestion import ForecastBatch, OpenMeteoClient
from tests.test_fresh_forecast import NOW, POINT, sources


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def fixture_batch(*args, **kwargs):
    sleep(2)
    return ForecastBatch(sources(),())


app.datetime=Clock
uk_map_grid.forecast_locations=lambda:(POINT,)
OpenMeteoClient.fetch_batched=fixture_batch
app.main()
st.caption('Browser verification only: synthetic 6–10 November weather with UKMO/ICON gaps. No live weather or notifications.')
