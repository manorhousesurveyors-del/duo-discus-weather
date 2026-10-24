"""Isolated browser test app: never contacts Gemini or uses a real credential.

Start with a DUO_DASHBOARD_SNAPSHOT and port 8505, then run
scripts/verify_gemini_browser.py. Not used by the production app.
"""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gemini_rest import GeminiError,GeminiREST
import streamlit as st
from app import main
import os
os.environ['GEMINI_API_KEY']='fixture-only'
if not os.environ.get('DUO_DASHBOARD_SNAPSHOT'):
    raise RuntimeError('Set DUO_DASHBOARD_SNAPSHOT to a recorded test weather bundle.')

def provider(self,path,body=None,**kwargs):
    if path=='models':return {'models':[{'name':'models/'+n,'supportedGenerationMethods':['generateContent']} for n in ('gemini-3.1-pro-preview','gemini-3-flash-preview')]}
    if '3.1-pro' in path:raise GeminiError(404,'Fixture: selected model is not enabled')
    request=json.loads(body['contents'][-1]['parts'][0]['text'])
    assert request['question']
    local=request['evidence']['scope']['kind']=='local'
    answer={'answer':'Browser fixture · Gemini REST. Denbigh has a westerly ridge-facing flow in the supplied forecast. Compare the hourly cloud base and gusts before widening your task. The Mynd northern ridge route is an option to assess.',
        'suggested_routes':[{'name':'Mynd northern ridge option','reason':'Shorten the route while reviewing the hourly weather.','codes':['MYN','MNDN','MYN']}]}
    if local:answer={'answer':'Browser fixture · Gemini REST. Denbigh’s local sector has a westerly flow in the supplied forecast. Check the hourly wind, cloud base and gusts.','suggested_routes':[]}
    return {'candidates':[{'content':{'parts':[{'text':json.dumps(answer)}]}}]}
# This isolated server also executes fragment reruns after main() returns.
# Keep its provider replaced for the process lifetime; context-manager patches
# can be restored by an interrupted prior Streamlit run.
GeminiREST.request=provider
main()
st.info('Browser verification only: model discovery and generation use a clearly labelled mock Gemini REST provider.')
