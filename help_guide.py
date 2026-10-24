"""Short, left-aligned help cards informed by BDA style guidance."""
import html
import re
import streamlit as st
from briefing_bot import answer_question, parse_intent

TOPICS = (
    ('📱 Get Free Phone Alerts', (
        ('Step 1 · Get the app', 'Install the free ntfy app on your phone. Allow notifications.'),
        ('Step 2 · Subscribe', 'Choose Subscribe. Use server https://ntfy.sh and topic g-ckml-soaring-alerts. No API key is needed.'),
        ('Step 3 · Read your alerts', 'You get a short message when the weather triggers an alert. Read Gmail for details. This topic is public.'))),
    ('☀ Quick Start: Check Today’s Flying Weather in 30 Seconds', (
        ('Step 1 · Pick your day', 'Choose the forecast day and time. Select Dry if you carry no water ballast.'),
        ('Step 2 · Open Map modes', 'Choose Thermal Lift or Potential Flight Distance. Hover over coloured areas for numbers.'),
        ('Step 3 · Check before you plan', 'Read the day estimate. Check cloud height, wind and rain. Missing weather means unknown.'))),
    ('🎨 What the Colours Mean', (
        ('Step 1 · Thermals and distance', 'Purple and blue mean less lift or distance. Green, yellow and red mean more. Clear areas may mean poor weather or missing data.'),
        ('Step 2 · Wind', 'Blue means lighter wind. Yellow, red and purple mean stronger wind. White trails move with the air.'),
        ('Step 3 · Radar', 'The blue radar picture shows recent rain. Click for a model’s hourly rain estimate. The coloured bar marks this estimate. It uses your chosen forecast hour.'))),
    ('📍 How to Build a Glider Task on the Map', (
        ('Step 1 · Start your route', 'Open Route Forecast Planner. Zoom in. Click a BGA marker for your start.'),
        ('Step 2 · Follow the dashed line', 'Move your mouse. The dashed line shows distance and direction. Click your next point. Undo removes your last choice.'),
        ('Step 3 · Finish and check', 'Choose Finish here or Return home. Read the verdict and start window. Your task sheet opens below the map.'))),
    ('🗺 Find Lift While You Plan', (
        ('Step 1 · Open Layers', 'Use the button at the top right of the Task Map. Choose Speed to Fly.'),
        ('Step 2 · Compare the colours', 'Hover near a weather sample. STF means the suggested speed between climbs. PFD means possible flight distance.'),
        ('Step 3 · Save your choices', 'Map clicks change your browser draft straight away. Finish or Analyse saves it. Tables below describe the last saved route.'))),
    ('★ Read the Day’s Stars', (
        ('Step 1 · One or two stars', 'One star means very poor thermal flying. Two stars suggest local soaring or short tasks.'),
        ('Step 2 · Three to five stars', 'Three stars suggest 200–300 kilometres. Four suggest 350–500 kilometres. Five need strong conditions for 500 kilometres or more.'),
        ('Step 3 · Check the details', 'Weak lift, low clouds and short windows reduce stars. Wave potential is separate. These are estimates, not promises.'))),
    ('〰 Read Wave Lift and Sink', (
        ('Step 1 · Choose your height', 'Open Wave watch. Click a height on the left. Its badge shows the strongest estimate nearby.'),
        ('Step 2 · Read both colours', 'Blue means sinking air. Yellow, orange and red mean rising air. Clear areas may be neutral or unknown.'),
        ('Step 3 · Look through the wave', 'Click a hill for a side view. Use Draw Wave X-Section to drag your own line. The model cannot locate safe wave entry or rotor.'))),
    ('✈ Watch Live Gliders', (
        ('Step 1 · Open glider traffic', 'Open Live OGN Glider Radar. Or choose Live OGN Gliders in a map’s Layers menu.'),
        ('Step 2 · Read a glider', 'Hover for height, speed and climb. Yellow outlines mark G-CKML. Choose a 15, 20 or 30 second refresh.'),
        ('Step 3 · Check Live or Demo', 'Demo shows pretend gliders for practice. Live hides old reports. Coverage has gaps. Never use this map to avoid other aircraft.'))),
    ('💧 What Wing Loading to Pick', (
        ('Step 1 · Dry means no water ballast', 'Dry uses 34 kilograms per square metre. This is the lighter glider setting.'),
        ('Step 2 · Water makes the glider heavier', 'Ballasted uses 43 kilograms per square metre. Strong lift may support faster cruising.'),
        ('Step 3 · Match your real aircraft', 'Use the loading that matches your flight. Follow the aircraft manual and your instructor’s advice.'))),
    ('⬇ Download Your Task to Paper or Your Cockpit PDA', (
        ('Step 1 · Open your task sheet', 'Finish your route first. Check the points, weather and turning zones.'),
        ('Step 2 · Choose a file', 'PDF is ready to print. Word lets you edit notes. CUP suits SeeYou or Oudie. TSK suits XCSoar.'),
        ('Step 3 · Send it to your instrument', 'Scan the QR code or open its link. Your phone must reach this website. Import the file. Check every point and zone.'))),
    ('📖 Small Gliding Word Guide', (
        ('Step 1 · Lift, sink and cloud base', 'Lift is rising air. Sink is falling air. Cloud base is the height where clouds begin.'),
        ('Step 2 · XC speed and speed to fly', 'XC means cross-country. XC speed includes climbing time. Speed to fly means your airspeed between thermals.'),
        ('Step 3 · PFD and the verdict', 'PFD estimates distance from local weather. Achievable means the model found enough time. It does not check airspace or landing fields.'))),
)


def easy_answer(text):
    """Keep evidence and numbers, split long briefings into readable steps."""
    replacements={'conservative climb':'careful climb estimate','usable depth AGL':'usable height above the ground',
                  'AMSL':'above sea level','spatial weather coverage':'weather coverage',
                  'MacCready cruise':'speed to fly','screening capacity':'first distance estimate',
                  'corridor':'route','heuristic':'rough guide','provisional':'not confirmed',
                  'operational':'flight','extrapolate':'guess beyond the data'}
    for a,b in replacements.items(): text=text.replace(a,b)
    sentences=re.split(r'(?<=[.!?])\s+|;\s+|\n\n+',text.strip())
    return '\n\n'.join(f'**Step {i+1}**\n\n{sentence}' for i,sentence in enumerate(sentences) if sentence)


def render_help(catalog,assessment,classification,waves,*,forecasts=(),day=None,today=None):
    st.subheader('Help Guide')
    st.caption('Small steps. Plain words. Take one card at a time.')
    st.markdown('''<style>
      .duo-help{font:18px Arial,Verdana,sans-serif;line-height:1.75;letter-spacing:.04em;word-spacing:.08em;color:#1E242B;text-align:left;background:#FDFBF7;border-radius:16px;padding:20px;margin:12px 0}
      .duo-help h3{font-size:21px;line-height:1.6;letter-spacing:.02em;color:#1E242B;margin:0 0 12px}
      .duo-help-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px}
      .duo-help-card{background:#F4F6F9;border-radius:12px;padding:18px}.duo-help-card b{display:block;margin-bottom:10px}.duo-help-card p{margin:0}
      .st-key-help_question,.st-key-help_answer{font:18px Arial,Verdana,sans-serif;line-height:1.75;letter-spacing:.04em;word-spacing:.08em}
      .st-key-help_answer [data-testid="stMarkdownContainer"] p{font-size:18px;line-height:1.75;background:#FDFBF7;color:#1E242B;padding:12px;border-radius:10px;text-align:left}
    </style>''',unsafe_allow_html=True)
    for title,cards in TOPICS:
        st.markdown('<section class="duo-help"><h3>'+html.escape(title)+'</h3><div class="duo-help-grid">'+
            ''.join('<article class="duo-help-card"><b>'+html.escape(label)+'</b><p>'+html.escape(body)+'</p></article>' for label,body in cards)+
            '</div></section>',unsafe_allow_html=True)
    st.subheader('Ask a Weather Question')
    st.caption('This helper uses the selected forecast and your current task.')
    question=st.text_input('What would you like explained?',key='help_question',placeholder='Why is my task marginal?',
        help='Ask about your task, today’s distance, wind, clouds or a BGA turnpoint. Answers use the selected model and task.')
    if st.button('Explain in small steps',key='help_ask',help='Show a short, step-by-step answer based on the current forecast.'):
        intent=parse_intent(question,catalog)
        if intent.kind=='suggest':text='Open Route Forecast Planner. Choose your distance. Click Optimise Route. The planner checks the weather before loading a route.'
        elif day is not None and intent.kind in ('weather','wave','day','help'):
            from briefing_bot import pilot_briefing,briefing_date
            text,_=pilot_briefing(question,forecasts,briefing_date(question,day,today or day))
        else:text=answer_question(intent,catalog,assessment,classification,waves)
        st.session_state['easy_briefing']=easy_answer(text)
    with st.container(key='help_answer'):
        if st.session_state.get('easy_briefing'):st.markdown(st.session_state['easy_briefing'])
    st.caption('Style informed by the British Dyslexia Association: clear type, left alignment, generous spacing and short sections.')
    st.link_button('BDA readable text guidance','https://www.bdadyslexia.org.uk/advice/employers/creating-a-dyslexia-friendly-workplace/dyslexia-friendly-style-guide',
                   help='Read the accessibility guidance used for this page.')
