# This file contains unit tests for the MatchingService.
# The tests are based on the acceptance criteria and scenarios
# outlined in the technical design document.

import unittest
from unittest.mock import MagicMock
from matching_service import MatchingService
from datetime import datetime, timedelta

class TestMatchingService(unittest.TestCase):
    """
    Unit tests for the MatchingService scoring functions.
    """
    def setUp(self):
        """
        Set up a mock database connection and the service instance.
        """
        self.mock_db = MagicMock()
        self.service = MatchingService(self.mock_db)

    def test_cost_score_within_budget(self):
        candidate = {'hourly_rate': 100}
        job = {'price_policy': {'min': 80, 'max': 120}}
        result = self.service.compute_cost_score(candidate, job)
        self.assertEqual(result['score'], 1.0)

    def test_cost_score_over_budget(self):
        candidate = {'hourly_rate': 150}
        job = {'price_policy': {'min': 80, 'max': 100}}
        result = self.service.compute_cost_score(candidate, job)
        self.assertAlmostEqual(result['score'], 1 / 1.5)

    def test_cost_score_under_budget(self):
        candidate = {'hourly_rate': 70}
        job = {'price_policy': {'min': 80, 'max': 120}}
        result = self.service.compute_cost_score(candidate, job)
        self.assertEqual(result['score'], 0.95)

    def test_place_score_remote_ok(self):
        candidate = {'remote_ok': True}
        job = {'location_policy': 'remote'}
        result = self.service.compute_place_score(candidate, job)
        self.assertEqual(result['score'], 1.0)

    def test_place_score_onsite_within_radius(self):
        candidate = {'location_points': [{'point': {'lat': 40.7128, 'lon': -74.0060}}]}
        job = {'location_policy': 'onsite',
               'location_point': {'lat': 40.730610, 'lon': -73.935242},
               'location_radius_km': 50}
        result = self.service.compute_place_score(candidate, job)
        self.assertEqual(result['score'], 1.0)

    def test_place_score_onsite_far_outside_radius(self):
        candidate = {'location_points': [{'point': {'lat': 34.0522, 'lon': -118.2437}}]}
        job = {'location_policy': 'onsite',
               'location_point': {'lat': 40.7128, 'lon': -74.0060},
               'location_radius_km': 50}
        result = self.service.compute_place_score(candidate, job)
        self.assertEqual(result['score'], 0)

    def test_place_score_onsite_moderately_outside_radius(self):
        candidate = {'location_points': [{'point': {'lat': 39.9526, 'lon': -75.1652}}]}
        job = {'location_policy': 'onsite',
               'location_point': {'lat': 40.7128, 'lon': -74.0060},
               'location_radius_km': 50}
        result = self.service.compute_place_score(candidate, job)
        self.assertTrue(0 < result['score'] < 1.0, f"Score was {result['score']}, expected between 0 and 1.")

    def test_time_score_full_overlap(self):
        job = {'schedule_requirements': {'windows': [{'start': '2024-01-01T09:00:00', 'end': '2024-01-01T17:00:00'}]}}
        candidate = {'availability': [{'start_ts': '2024-01-01T09:00:00', 'end_ts': '2024-01-01T17:00:00'}]}
        result = self.service.compute_time_score(candidate, job)
        self.assertEqual(result['score'], 1.0)

    def test_time_score_partial_overlap(self):
        job = {'schedule_requirements': {'windows': [{'start': '2024-01-01T09:00:00', 'end': '2024-01-01T17:00:00'}]}} # 8 hours
        candidate = {'availability': [{'start_ts': '2024-01-01T13:00:00', 'end_ts': '2024-01-01T17:00:00'}]} # 4 hours
        result = self.service.compute_time_score(candidate, job)
        self.assertAlmostEqual(result['score'], 0.5)

    def test_time_score_no_overlap(self):
        job = {'schedule_requirements': {'windows': [{'start': '2024-01-01T09:00:00', 'end': '2024-01-01T17:00:00'}]}}
        candidate = {'availability': [{'start_ts': '2024-01-02T09:00:00', 'end_ts': '2024-01-02T17:00:00'}]}
        result = self.service.compute_time_score(candidate, job)
        self.assertEqual(result['score'], 0.0)

    def test_time_score_with_timezone_penalty(self):
        job = {'schedule_requirements': {'windows': [{'start': '2024-01-01T09:00:00', 'end': '2024-01-01T17:00:00'}]}, 'timezone_offset': 0}
        candidate = {'availability': [{'start_ts': '2024-01-01T09:00:00', 'end_ts': '2024-01-01T17:00:00'}], 'timezone_offset': 10}
        result = self.service.compute_time_score(candidate, job)
        # 1.0 (overlap) * (1 - (10 - 3) / 21) = 1 * (1 - 7/21) = 1 * (1 - 1/3) = 2/3
        self.assertAlmostEqual(result['score'], 2/3)

    def test_experience_score_full_match(self):
        candidate = {'domains': [{'domain': 'fintech', 'years': 5, 'seniority': 'senior'}], 'certs': [{'cert_code': 'ISO9001'}]}
        job = {'experience_requirements': [{'domain': 'fintech', 'min_years': 3}], 'mandatory_flags': ['cert:ISO9001']}
        result = self.service.compute_experience_score(candidate, job, skill_overlap=1.0)
        # 0.55 * 1.0 (skill) + 0.25 * 1.0 (domain) + 0.15 * 1.0 (seniority) + 0.15 (cert) = 1.1 -> clamped to 1.0
        self.assertEqual(result['score'], 1.0)

    def test_experience_score_partial_match(self):
        candidate = {'domains': [{'domain': 'fintech', 'years': 2, 'seniority': 'junior'}], 'certs': []}
        job = {'experience_requirements': [{'domain': 'fintech', 'min_years': 4}], 'mandatory_flags': []}
        result = self.service.compute_experience_score(candidate, job, skill_overlap=0.6)
        # 0.55 * 0.6 + 0.25 * (2/4) + 0.15 * 0.4 + 0 = 0.33 + 0.125 + 0.06 = 0.515
        self.assertAlmostEqual(result['score'], 0.515)

    def test_experience_score_no_domain_match(self):
        candidate = {'domains': [{'domain': 'healthcare', 'years': 10, 'seniority': 'lead'}], 'certs': []}
        job = {'experience_requirements': [{'domain': 'fintech', 'min_years': 4}]}
        result = self.service.compute_experience_score(candidate, job, skill_overlap=0.2)
        # 0.55 * 0.2 + 0.25 * 0 + 0.15 * 0.4 (default junior) = 0.11 + 0.06 = 0.17
        self.assertAlmostEqual(result['score'], 0.17)

if __name__ == '__main__':
    unittest.main()
