from dataclasses import replace
import unittest
from unittest.mock import patch

from bga_turnpoints import load_catalog
from briefing_bot import answer_question, explain_task, parse_intent, shortlist_tasks
from task_planning import assess_task, classify_day
from tests.test_task_planning import TASK, DAY, A, weather


class BriefingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.catalog=load_catalog()

    def test_intents_resolve_origin_target_shape_and_explicit_load(self):
        intent=parse_intent('Find and load a 300 km triangle from MYN',self.catalog)
        self.assertEqual((intent.kind,intent.target_km,intent.origin_code,intent.shape,intent.load_best),
                         ('suggest',300,'MYN','triangle',True))
        self.assertFalse(parse_intent('Suggest a 250 km task',self.catalog).load_best)
        for text,kind in [('Why is my task marginal?','explain'),('Cloud streets along my task','weather'),
                          ('What kind of day is it?','day'),('Where is MYN?','turnpoint'),('Is there wave?','wave')]:
            self.assertEqual(parse_intent(text,self.catalog).kind,kind)

    def test_answers_use_catalog_elevation_and_live_assessment(self):
        intent=parse_intent('where is MYN?',self.catalog)
        answer=answer_question(intent,self.catalog,None,None)
        self.assertIn('52.51865',answer);self.assertIn('426 m AMSL',answer)
        result=assess_task(TASK,weather(),DAY)
        answer=explain_task(result)
        self.assertIn(result.verdict,answer);self.assertIn(result.best.weakest_leg,answer)
        answer=explain_task(assess_task(TASK,(),DAY))
        self.assertIn('INSUFFICIENT DATA',answer);self.assertIn('missing',answer)
        answer=explain_task(assess_task(TASK,weather(precipitation=2),DAY))
        self.assertIn('NOT ACHIEVABLE',answer);self.assertIn('blocks',answer)

    def test_no_false_weather_or_wave_answer_without_required_data(self):
        self.assertIn('No complete wave',answer_question(parse_intent('wave',self.catalog),self.catalog,None,None))
        self.assertIn('needs a complete',answer_question(parse_intent('day',self.catalog),self.catalog,None,None))
        self.assertIn('Build or load',explain_task(None))

    def test_shortlist_is_bounded_and_screening_does_not_authorize_loading(self):
        origin=self.catalog.by_code['MYN']
        tasks=shortlist_tasks(self.catalog,origin,250,(),DAY,'dry',preference=('ecmwf_ifs025',),shape='triangle')
        self.assertLessEqual(len(tasks),3);self.assertTrue(tasks)
        for task in tasks:
            self.assertEqual(len(task),4)
            self.assertIsNone(assess_task(task,(),DAY).best)
