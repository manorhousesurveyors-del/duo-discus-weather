"""Real live-mode engine with synthetic HTTP success, 429 or slow timeout.

DUO_TEST_SCENARIO selects the boundary. No weather, OGN or alert traffic leaves
this fixture. Use verify_architecture.py; never deploy this file as the app.
"""
import os,sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['DUO_OGN_ENABLED']='0'
os.environ['DUO_DASHBOARD_SNAPSHOT']=''
import streamlit as st
import app,weather_engine
from tests.provider_fixture import Provider,NOW

class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

@st.cache_resource
def install():
    scenario=os.environ.get('DUO_TEST_SCENARIO','healthy')
    provider=Provider(scenario,2.5 if scenario=='timeout' else .1)
    weather_engine._store=weather_engine.ResourceStore()
    weather_engine._transport=weather_engine.ForecastHttpClient(opener=provider,clock=lambda:NOW,
        gate=weather_engine.ProviderGate(clock=lambda:0.,budget=100000))
    return provider

provider=install()
app.datetime=Clock
app.main()
st.caption('Fixture server render ms: '+str(st.session_state['_page_render_ms']))
st.caption('Fixture HTTP requests: '+str(len(provider.calls)))
st.caption('Synthetic browser test only · '+provider.scenario)
