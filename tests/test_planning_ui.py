import ast
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import app
import bga_turnpoints as b
import planning_ui as ui
from alerts import ForecastBundle, save_bundle
from config import forecast_locations
from tests.fixtures import NOW, forecast
from tests.test_task_planning import A, B, DAY, TASK


class PlanningUiTests(unittest.TestCase):
    def test_all_controls_metrics_and_map_markers_have_hover_help(self):
        controls={'selectbox','multiselect','slider','radio','toggle','text_input','button','download_button','file_uploader','metric','checkbox'}
        for path in ('app.py','planning_ui.py','route_tools.py','help_guide.py','route_analysis.py'):
            tree=ast.parse(Path(path).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    if node.func.attr in controls:
                        self.assertIn('help',{k.arg for k in node.keywords},f'{path}:{node.lineno}')
                    if node.func.attr in {'Marker','CircleMarker','PolyLine','Rectangle'}:
                        self.assertIn('tooltip',{k.arg for k in node.keywords},f'{path}:{node.lineno}')

    def test_table_refuses_unexplained_columns(self):
        with self.assertRaisesRegex(ValueError,'Missing table column help'):
            ui.weather_table([{'Unexplained':1}],{})

    def test_marker_coordinate_and_layer_events_are_idempotent(self):
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'DUO_TASK_STORE':directory}),patch.object(ui.st,'session_state',{}):
            click={'last_object_clicked':{'lat':A.latitude,'lng':A.longitude},'last_object_clicked_count':1,
                   'last_object_clicked_tooltip':'BGA AAA · Test start. Click to add.'}
            ui.process_map_event(click,catalog,DAY,'Add to task')
            ui.process_map_event(click,catalog,DAY,'Add to task')
            self.assertEqual(ui.active_task(DAY),(A,))
            ui.process_map_event({**click,'last_clicked':{'lat':53,'lng':-3}},catalog,DAY,'Add to task')
            self.assertEqual(len(ui.active_task(DAY)),2)
            self.assertFalse(ui.active_task(DAY)[1].official)
            ui.process_map_event({'selected_tags':['duo:thermals:on']},catalog,DAY,'Inspect sounding')
            self.assertIn('Thermals',ui.st.session_state['map_layers'])
            ui.process_map_event({'selected_tags':['duo:thermals:off']},catalog,DAY,'Inspect sounding')
            self.assertNotIn('Thermals',ui.st.session_state['map_layers'])

    def test_absolute_thermal_palette_and_transparent_uncovered_pixels(self):
        for value,colour in app.THERMAL_STOPS:
            self.assertEqual(app.thermal_colour(value),colour)
        self.assertEqual(app.thermal_colour(-2),app.THERMAL_STOPS[0][1])
        self.assertEqual(app.thermal_colour(9),app.THERMAL_STOPS[-1][1])
        with patch('global_land_mask.globe.is_land',side_effect=lambda lat,lon:np.ones(lat.shape,dtype=bool)):
            rgba,bounds=app.thermal_overlay_data([[52,-3,3],[53,-3,3],[52,-2,3],[53,-2,3]])
        opaque=rgba[rgba[:,:,3]>0]
        self.assertTrue(len(opaque));self.assertTrue(np.all(opaque==[244,230,90,155]))
        self.assertTrue(np.any(rgba[:,:,3]==0))
        self.assertIsNone(app.thermal_overlay_data([[52,-3,1],[53,-3,2],[54,-3,3]]))

    def test_clean_map_and_explicit_task_markers(self):
        with patch('app.thermal_overlay_data',return_value=None):
            clean,_=app.build_map((),NOW,layers=['BGA turnpoints'],turnpoints=(A,B))
            active,_=app.build_map((),NOW,layers=[],task=TASK)
        clean_html=clean.get_root().render();active_html=active.get_root().render()
        self.assertIn('BGA AAA',clean_html);self.assertNotIn('Active task',clean_html)
        self.assertIn('duo_active_task_v1',active_html)
        self.assertIn('Clear Task',active_html)
        self.assertIn('\"code\":\"AAA\"',active_html)
        self.assertIn('DuoCanvasPoints',clean_html);self.assertNotIn('leaflet.markercluster',clean_html)

    def test_builder_sequence_reorder_clear_and_day_switch(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory:
            snapshot=Path(directory)/'snapshot.json'
            save_bundle(ForecastBundle(tuple(forecast(p) for p in forecast_locations()),(),.5,(),NOW),snapshot)
            with patch.dict('os.environ',{'DUO_TASK_STORE':directory+'/tasks','DUO_DASHBOARD_SNAPSHOT':str(snapshot)}):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=30).run()
                at.radio(key='active_page').set_value('Task planner').run()
                at.session_state['sidebar_task_builder_open']=True
                at.run()
                self.assertFalse(at.exception)
                seq=next(w for w in at.text_input if w.key.startswith('sidebar_') and w.key.endswith('_sequence'))
                prefix=seq.key.removesuffix('_sequence')
                seq.set_value('MYN BIE MYN').run()
                at.button(key=prefix+'_sequence_load').click().run()
                self.assertFalse(at.exception)
                points=at.session_state['tasks_by_day'][prefix.removeprefix('sidebar_')]
                self.assertEqual([p.code for p in points],['MYN','BIE','MYN'])
                self.assertEqual(len(at.get('download_button')),6)
                original_day=at.selectbox(key='forecast_day').value
                at.selectbox(key='forecast_day').select(original_day+1).run()
                self.assertFalse(at.exception)
                self.assertEqual(len(at.get('download_button')),0)
                at.selectbox(key='forecast_day').select(original_day).run()
                self.assertEqual(len(at.get('download_button')),6)
                at.selectbox(key=prefix+'_edit').select(1).run()
                at.button(key=prefix+'_up').click().run()
                self.assertFalse(at.exception)
                self.assertEqual(at.session_state['tasks_by_day'][prefix.removeprefix('sidebar_')][0].code,'BIE')
                at.button(key=prefix+'_clear').click().run()
                self.assertFalse(at.exception)
                self.assertEqual(at.session_state['tasks_by_day'][prefix.removeprefix('sidebar_')],())

    def test_bot_proposals_show_verdict_and_only_load_after_explicit_click(self):
        from streamlit.testing.v1 import AppTest
        from tests.test_task_planning import weather
        script='''
from datetime import date
import bga_turnpoints as b
from planning_ui import briefing_panel
briefing_panel(b.load_catalog(),date(2026,6,10),'dry','ifs025',1,(),None,None,(),recorded=True)
'''
        for available in (True,False):
            with self.subTest(available=available),tempfile.TemporaryDirectory() as directory, \
                 patch.dict('os.environ',{'DUO_TASK_STORE':directory}), \
                 patch.object(ui,'shortlist_tasks',return_value=(TASK,)), \
                 patch.object(ui,'task_weather',return_value=(weather() if available else (),())):
                at=AppTest.from_string(script).run()
                next(button for button in at.button if button.label=='Suggest 250 km').click().run()
                self.assertFalse(at.exception)
                self.assertFalse(list(Path(directory).glob('*.json')))
                load=next(button for button in at.button if button.label=='Load suggested route onto map')
                self.assertFalse(load.disabled)
                if available:
                    load.click().run()
                    self.assertFalse(at.exception)
                    self.assertEqual([p.code for p in at.session_state['tasks_by_day']['2026-06-10']],['AAA','BBB','AAA'])
                else:
                    self.assertIn('unknown',at.session_state['briefing_messages'][-1]['content'])
                    self.assertIn('INSUFFICIENT DATA',str(at.session_state['assistant_routes']))

    def test_rendered_thermal_png_preserves_physical_palette(self):
        import base64
        import io
        import re
        from PIL import Image
        rgba=np.full((4,4,4),[244,230,90,155],dtype=np.uint8)
        with patch('app.thermal_overlay_data',return_value=(rgba,[[52,-3],[53,-2]])):
            map_view,_=app.build_map((),NOW,layers=['Thermals'])
        document=map_view.get_root().render()
        encoded=re.search(r'data:image/png;base64,([^"\s]+)',document).group(1)
        image=np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded))))
        self.assertTrue(np.all(image==[244,230,90,155]))

    def test_cluster_identifier_wins_over_bubbled_screen_coordinate(self):
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        event={'selected_tags':['duo:point:AAA:123'], 'last_clicked':{'lat':52.49,'lng':-2.81},
               'last_object_clicked':None}
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'DUO_TASK_STORE':directory}),patch.object(ui.st,'session_state',{}):
            ui.process_map_event(event,catalog,DAY,'Add to task')
            ui.process_map_event(event,catalog,DAY,'Add to task')
            self.assertEqual(ui.active_task(DAY),(A,))

    def test_floating_modes_and_route_actions_are_idempotent(self):
        from route_tools import is_complete
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        with tempfile.TemporaryDirectory() as directory,patch.dict('os.environ',{'DUO_TASK_STORE':directory}),patch.object(ui.st,'session_state',{}):
            event={'selected_tags':['duo:mode:route:1']}
            ui.process_map_event(event,catalog,DAY,'Inspect weather')
            self.assertEqual(ui.st.session_state['visual_mode'],'route')
            for n,point in enumerate((A,B)):
                ui.process_map_event({'selected_tags':[f'duo:point:{point.code}:{n}']},catalog,DAY,'Inspect weather')
            self.assertEqual(ui.active_task(DAY),(A,B))
            home={'selected_tags':['duo:action:home:3']}
            ui.process_map_event(home,catalog,DAY,'Inspect weather')
            ui.process_map_event(home,catalog,DAY,'Inspect weather')
            self.assertEqual(ui.active_task(DAY),TASK);self.assertTrue(is_complete(DAY,TASK))
            ui.process_map_event({'selected_tags':['duo:action:undo:4']},catalog,DAY,'Inspect weather')
            self.assertEqual(ui.active_task(DAY),(A,B));self.assertFalse(is_complete(DAY,(A,B)))
            ui.process_map_event({'selected_tags':['duo:action:clear:5']},catalog,DAY,'Inspect weather')
            self.assertEqual(ui.active_task(DAY),())

    def test_wind_click_does_not_append_to_task_or_override_inspector(self):
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        with patch.object(ui.st,'session_state',{'visual_mode':'wind'}):
            ui.process_map_event({'last_clicked':{'lat':52,'lng':-3}},catalog,DAY,'Add to task')
            self.assertNotIn('inspector_coordinate',ui.st.session_state)
            self.assertNotIn('tasks_by_day',ui.st.session_state)

    def test_radar_click_uses_exact_tag_coordinate_without_changing_task(self):
        catalog=b.Catalog((A,B),'test','',b.content_hash((A,B)))
        with patch.object(ui.st,'session_state',{'visual_mode':'radar'}):
            event={'selected_tags':['duo:radar:52.518:-2.88:123'],
                   'last_clicked':{'lat':52,'lng':-3}}
            ui.process_map_event(event,catalog,DAY,'Add to task')
            ui.process_map_event(event,catalog,DAY,'Add to task')
            self.assertEqual(ui.st.session_state['radar_coordinate'],(52.518,-2.88))
            self.assertNotIn('inspector_coordinate',ui.st.session_state)
            self.assertNotIn('tasks_by_day',ui.st.session_state)
            ui.process_map_event({'last_clicked':{'lat':53,'lng':-2}},catalog,DAY,'Add to task')
            self.assertEqual(ui.st.session_state['radar_coordinate'],(53,-2))
            ui.process_map_event({'selected_tags':['duo:radar:nan:0:124']},catalog,DAY,'Add to task')
            self.assertEqual(ui.st.session_state['radar_coordinate'],(53,-2))

    def test_optimizer_ui_loads_verified_candidate_and_opens_exports(self):
        from streamlit.testing.v1 import AppTest
        from task_planning import corridor_points,task_distance
        from route_tools import build_exports
        build_exports.clear()
        with tempfile.TemporaryDirectory() as directory:
            snapshot=Path(directory)/'snapshot.json'
            save_bundle(ForecastBundle(tuple(forecast(p,start=NOW.replace(hour=0)) for p in forecast_locations()),(),.5,(),NOW),snapshot)
            def corridor(points,*args,**kwargs):
                return tuple(forecast(p,start=NOW.replace(hour=0)) for p in corridor_points(points)),()
            with patch.dict('os.environ',{'DUO_TASK_STORE':directory+'/tasks','DUO_DASHBOARD_SNAPSHOT':str(snapshot)}), \
                 patch('planning_ui.task_weather',side_effect=corridor), \
                 patch('route_tools.publish_downloads',return_value='app/static/tasks/test/duo-task'):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=40).run()
                at.radio(key='active_page').set_value('Task planner').run()
                at.button(key='optimise_route').click().run()
                self.assertFalse(at.exception)
                tasks=list(at.session_state['tasks_by_day'].values());points=next(p for p in tasks if p)
                self.assertGreaterEqual(task_distance(points),200)
                self.assertLessEqual(task_distance(points),216)
                self.assertEqual(points[0],points[-1])
                self.assertIn('Loaded',at.session_state['optimiser_result'])
                self.assertTrue(any(e.label=='BGA / SeeYou Task Sheet' for e in at.expander))
                self.assertEqual(len(at.get('download_button')),6)
                at.radio(key='active_page').set_value('Help Guide').run()
                at.text_input(key='help_question').set_value('Why is my task achievable?').run()
                at.button(key='help_ask').click().run()
                self.assertFalse(at.exception)
                self.assertIn('Step 1',at.session_state['easy_briefing'])
