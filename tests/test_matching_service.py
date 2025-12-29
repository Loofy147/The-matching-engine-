# This file contains unit tests for the MatchingService.
# The tests are based on the acceptance criteria and scenarios
# outlined in the technical design document.

import unittest
from unittest.mock import MagicMock, patch
from matching_service import MatchingService
from cache_client import CacheClient

class TestMatchingService(unittest.TestCase):
    """
    Unit tests for the MatchingService scoring functions and caching logic.
    """
    def setUp(self):
        """
        Set up mock dependencies for the service instance.
        """
        self.mock_db = MagicMock()
        self.mock_cache = MagicMock(spec=CacheClient)
        self.service = MatchingService(self.mock_db, self.mock_cache)

        # Define a sample job_id for use in tests
        self.job_id = "a4e6a2b2-5183-4265-878d-174a1592333b"

    def test_match_job_cache_hit(self):
        """
        Scenario: Matching results for a job are found in the cache.
        Expected: The service should return the cached results directly without querying the DB.
        """
        # Arrange: Mock the cache to return a pre-defined list of matches
        cached_matches = [{'user_id': 'user1', 'final_score': 0.95}]
        self.mock_cache.get.return_value = cached_matches

        # Act: Call the main matching function
        results = self.service.match_job(self.job_id)

        # Assert:
        # 1. The results match the cached data.
        self.assertEqual(results, cached_matches)
        # 2. The cache was checked with the correct key.
        self.mock_cache.get.assert_called_once_with(f"matches:{self.job_id}")
        # 3. The database was NOT queried for initial candidates.
        self.mock_db.execute_sql_file.assert_not_called()

    def test_match_job_cache_miss(self):
        """
        Scenario: No matching results are in the cache.
        Expected: The service should execute the full matching logic and store the result in the cache.
        """
        # Arrange:
        # 1. Mock the cache to return nothing (a cache miss)
        self.mock_cache.get.return_value = None
        # 2. Mock the full pipeline of DB calls and service computations
        self.mock_db.execute_sql_file.return_value = [{'user_id': 'user1', 'experience_score': 0.8}]
        self.mock_db.find_job_by_id.return_value = {'id': self.job_id}
        self.mock_db.get_user_full_profiles_batch.return_value = {'user1': {'id': 'user1'}}
        self.mock_db.get_match_weights.return_value = {'time': 0.2, 'place': 0.1, 'cost': 0.3, 'experience': 0.4}

        # Act: Call the main matching function
        results = self.service.match_job(self.job_id)

        # Assert:
        # 1. The cache was checked first.
        self.mock_cache.get.assert_called_once_with(f"matches:{self.job_id}")
        # 2. Since it was a miss, the database was queried.
        self.mock_db.execute_sql_file.assert_called_once()
        # 3. The final results were stored in the cache.
        self.mock_cache.set.assert_called_once()
        # 4. We got a non-empty list of results.
        self.assertTrue(len(results) > 0)


    # --- Scoring Function Tests (Unaffected by caching) ---
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

    def test_time_score_full_overlap(self):
        job = {'schedule_requirements': {'windows': [{'start': '2024-01-01T09:00:00', 'end': '2024-01-01T17:00:00'}]}}
        candidate = {'availability': [{'start_ts': '2024-01-01T09:00:00', 'end_ts': '2024-01-01T17:00:00'}]}
        result = self.service.compute_time_score(candidate, job)
        self.assertEqual(result['score'], 1.0)

    def test_experience_score_full_match(self):
        candidate = {'domains': [{'domain': 'fintech', 'years': 5, 'seniority': 'senior'}], 'certs': [{'cert_code': 'ISO9001'}]}
        job = {'experience_requirements': [{'domain': 'fintech', 'min_years': 3}], 'mandatory_flags': ['cert:ISO9001']}
        result = self.service.compute_experience_score(candidate, job, skill_overlap=1.0)
        self.assertEqual(result['score'], 1.0)


if __name__ == '__main__':
    unittest.main()
