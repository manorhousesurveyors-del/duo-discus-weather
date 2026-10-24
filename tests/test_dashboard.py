from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app
from alerts import ForecastBundle, save_bundle
from config import forecast_locations
from data_ingestion import IngestionError, JsonResponse
from soaring_physics import Profile
from tests.fixtures import NOW, forecast


class DashboardTests(unittest.TestCase):
    def test_sounding_is_log_pressure_and_missing_humidity_stays_a_gap(self):
        p=Profile.from_forecast(forecast(changes={'relative_humidity_850hPa':None}),NOW)
        figure=app.sounding_figure(p)
        self.assertEqual(figure.layout.yaxis.type,'log')
        self.assertGreater(figure.layout.yaxis.range[0],figure.layout.yaxis.range[1])
        dew=next(t for t in figure.data if t.name=='Dewpoint')
        self.assertIn(None,dew.x)
        self.assertFalse(dew.connectgaps)

    def test_wind_barb_direction_and_fifty_knot_pennant(self):
        self.assertIn('rotate(270.0',app.wind_barb_svg(270,55,'#123456'))
        self.assertIn('<polygon',app.wind_barb_svg(270,55,'#123456'))
        self.assertIn('<circle',app.wind_barb_svg(None,None,'#123456'))

    def test_map_contains_layers_and_only_verified_asset_hosts(self):
        values=tuple(forecast(p) for p in forecast_locations())
        with patch('app.thermal_overlay_data',return_value=None):
            map_view,waves=app.build_map(values,NOW,layers=['Thermals','Cloud base','Wave sites'])
        document=map_view.get_root().render()
        for value in ('tile.openstreetmap.org','a.tile.opentopomap.org','cdn.jsdelivr.net',
                      'Thermal strength','Potential cloud base'):
            self.assertIn(value,document)
        self.assertEqual(len(waves),12)
        self.assertNotIn('Active task',document)
        self.assertNotIn('Duo Discus task templates',document)
        self.assertNotIn('rawcdn.githack.com',document)
        self.assertNotIn('d3js.org',document)

    def test_radar_rejects_foreign_host_and_malformed_frames(self):
        for body in ({'host':'https://example.test'},
                     {'host':'https://tilecache.rainviewer.com','radar':{'past':[{}]}}):
            app.load_radar.clear()
            with patch('app.JsonHttpClient') as client:
                client.return_value.get_json.return_value=JsonResponse(body,NOW)
                with self.assertRaises(IngestionError): app.load_radar(1)
        app.load_radar.clear()

    def test_streamlit_controls_tabs_and_unavailable_model(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.json'
            save_bundle(ForecastBundle(tuple(forecast(p) for p in forecast_locations()),(),.5,(),NOW),path)
            with patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':str(path),'DUO_TASK_STORE':directory+'/tasks'}):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=30).run()
                self.assertFalse(at.exception)
                self.assertEqual([tab.label for tab in at.tabs],['Weather map','Task planner','Help Guide','Wave watch','Help & Briefing Bot','Data quality'])
                self.assertEqual(len(at.metric),7)
                self.assertEqual(len(at.get('plotly_chart')),2)
                at.slider(key="forecast_hour").set_value(16).run()
                self.assertFalse(at.exception)
                self.assertIn('16:00',at.metric[3].value)
                at.selectbox(key='forecast_model').select('ukmo').run()
                self.assertFalse(at.exception)
                self.assertTrue(any("does not cover" in w.value.lower() for w in at.warning))

    def test_map_click_switches_inspector_to_coordinate(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.json'
            save_bundle(ForecastBundle(tuple(forecast(p) for p in forecast_locations()),(),.5,(),NOW),path)
            with patch.dict('os.environ',{'DUO_DASHBOARD_SNAPSHOT':str(path),'DUO_TASK_STORE':directory+'/tasks'}), \
                 patch('streamlit_folium.st_folium',return_value={'last_clicked':{'lat':53.,'lng':-2.5}}):
                at=AppTest.from_file(str(Path(app.__file__)),default_timeout=30).run()
                self.assertFalse(at.exception)
                self.assertEqual(at.selectbox(key='inspector_selection').value,'Map coordinate')
                self.assertEqual(len(at.get('plotly_chart')),2)
