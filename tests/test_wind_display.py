"""Verify the actual canvas drawing primitives, including meteorological units."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from client_map import wind_canvas_options


@unittest.skipUnless(shutil.which('node'),'Node is needed for the canvas glyph tests')
class WindGlyphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code=r'''
const fs=require('fs');eval(fs.readFileSync('assets/wind_glyphs.js','utf8'));
const map={};installDuoWindCanvas(map,OPTIONS);
class Canvas{
 constructor(){this.path=[];this.strokes=[];this.fills=[];this.arcs=[];this.rotations=[];}
 beginPath(){this.path=[];} moveTo(...p){this.path.push(['M',...p]);} lineTo(...p){this.path.push(['L',...p]);}
 closePath(){this.path.push(['Z']);} arc(...p){this.arcs.push(p);} save(){} restore(){} translate(){}
 rotate(a){this.rotations.push(a);} strokeText(){} fillText(){}
 stroke(){this.strokes.push({width:this.lineWidth,colour:this.strokeStyle,path:[...this.path]});}
 fill(){this.fills.push({colour:this.fillStyle,path:[...this.path]});}
}
const g=map._duoWindGlyphs,rows={};
for(const knots of [0,2,5,10,15,45,50,55,65,100,155]){
 const c=new Canvas(),result=g.barb(c,100,100,knots/1.943844492,0);rows[knots]={...result,strokes:c.strokes,fills:c.fills,arcs:c.arcs};
}
const a=new Canvas(),arrow=g.arrow(a,[[0,0],[10,0],[20,0]]);
const north=g.barb(new Canvas(),0,0,0,10);
console.log(JSON.stringify({rows,arrow,arrowStrokes:a.strokes,arrowFills:a.fills,northAngle:north.fromAngle,
 invalid:g.barbParts(NaN),calmArrow:g.arrow(new Canvas(),[[0,0],[0,0]])}));
'''.replace('OPTIONS',json.dumps(wind_canvas_options()))
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=10)
        if result.returncode:raise AssertionError(result.stderr)
        cls.result=json.loads(result.stdout)

    def test_standard_5_10_50_knot_symbols(self):
        for knots,expected in ((0,(0,0,0)),(5,(0,0,1)),(10,(0,1,0)),(15,(0,1,1)),
                               (45,(0,4,1)),(50,(1,0,0)),(55,(1,0,1)),(65,(1,1,1)),(100,(2,0,0)),(155,(3,0,1))):
            row=self.result['rows'][str(knots)];parts=row['parts']
            self.assertEqual((parts['pennants'],parts['full'],parts['half']),expected)
            self.assertEqual(len(row['fills']),expected[0])
            self.assertTrue(all(op['colour']=='#111827' for op in row['fills']))
        self.assertEqual(len(self.result['rows']['0']['arcs']),1)
        self.assertTrue(self.result['rows']['2']['parts']['calm'])
        self.assertIsNone(self.result['invalid']);self.assertIsNone(self.result['calmArrow'])

    def test_arrowheads_are_eight_by_five_and_follow_flow(self):
        arrow=self.result['arrow']
        self.assertEqual(arrow['tip'],[20,0]);self.assertEqual(arrow['head'],[[20,0],[12,2.5],[12,-2.5]])
        strokes=self.result['arrowStrokes']
        self.assertEqual(strokes[0]['width'],4.) # 2.4 + two 0.8 px halos
        self.assertEqual(strokes[0]['colour'],'rgba(255, 255, 255, 0.55)')
        self.assertEqual(strokes[1]['width'],2.4);self.assertEqual(strokes[1]['colour'],'#111827')
        self.assertEqual(self.result['arrowFills'][0]['colour'],'#111827')

    def test_barbs_point_into_the_wind_and_have_two_pixel_shafts(self):
        import math
        self.assertAlmostEqual(self.result['rows']['10']['fromAngle'],math.pi) # eastward flow -> west-facing staff
        self.assertAlmostEqual(self.result['northAngle'],math.pi/2) # northward flow -> south-facing staff
        strokes=self.result['rows']['10']['strokes']
        self.assertEqual(strokes[0]['width'],3.6);self.assertEqual(strokes[1]['width'],2.)
        self.assertEqual(strokes[1]['colour'],'#111827')
        # A lone half barb is set back from the tip, as on a synoptic chart.
        half=self.result['rows']['5']['strokes'][-1]['path']
        self.assertEqual(half,[['M',24,0],['L',21.5,5.5]])

    def test_generated_wind_map_installs_glyphs_before_display_and_contains_no_heat_canvas(self):
        from app import build_map
        from tests.fixtures import forecast,NOW
        view,_=build_map((forecast(),),NOW,layers=[],visual_mode='wind',live_airfields=False)
        html=view.get_root().render()
        self.assertLess(html.index('installDuoWindCanvas('+view.get_name()),html.index('installDuoModes('+view.get_name()))
        self.assertNotIn("canvas.style.filter='blur(8px)'",html)
        self.assertNotIn("create('canvas','duo-wind-heat'",html)
        self.assertIn('Wind Display: Streaming Arrows',html)
