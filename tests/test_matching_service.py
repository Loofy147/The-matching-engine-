# This file contains unit tests for the asynchronous MatchingService.
# Tests are adapted to verify both the job enqueuing mechanism and the
# core matching logic executed by the background worker.

import unittest
from unittest.mock import MagicMock, patch
from matching_service import MatchingService
from cache_client import CacheClient
from job_queue import JobQueue

class TestMatchingServiceAsync(unittest.TestCase):
    """
    Unit tests for the asynchronous MatchingService.
    """
    def setUp(self):
        """
        Set up mock dependencies for the service instance.
        """
        self.mock_db = MagicMock()
        self.mock_cache = MagicMock(spec=CacheClient)

        # We patch the job queue's __init__ to prevent it from starting a real background thread during tests.
        # The `autospec=True` ensures the mock has the same signature as the original.
        with patch('job_queue.JobQueue.__init__', autospec=True, return_value=None) as self.mock_job_queue_init:
            # We also mock the `enqueue_job` method on the JobQueue class itself.
            self.mock_enqueue = patch.object(JobQueue, 'enqueue_job').start()
            self.service = MatchingService(self.mock_db, self.mock_cache)

        self.job_id = "a4e6a2b2-5183-4265-878d-174a1592333b"

    def tearDown(self):
        # Stop any patches that were started in setUp
        patch.stopall()

    def test_trigger_match_job_enqueues_job(self):
        """
        Scenario: The public-facing trigger method is called.
        Expected: A job with the correct job_id is enqueued.
        """
        # Arrange: Set a return value for the mocked enqueue function
        self.mock_enqueue.return_value = {"status": "enqueued", "job_id": self.job_id}

        # Act: Call the trigger function
        result = self.service.trigger_match_job(self.job_id)

        # Assert:
        # 1. The enqueue method on the job queue was called once with the correct job_id.
        self.mock_enqueue.assert_called_once_with(self.job_id)
        # 2. The result from the trigger function matches the expected response.
        self.assertEqual(result, {"status": "enqueued", "job_id": self.job_id})

    def test_execute_match_job_logic_full_run(self):
        """
        Scenario: The background worker executes a matching task.
        Expected: The full matching logic runs, and results are saved and cached.
        """
        # Arrange: Mock the full pipeline of DB calls
        self.mock_db.execute_sql_file.return_value = [{'user_id': 'user1', 'experience_score': 0.8}]
        self.mock_db.find_job_by_id.return_value = {'id': self.job_id, 'experience_requirements': []}
        self.mock_db.get_user_full_profiles_batch.return_value = {'user1': {'id': 'user1', 'domains': []}}
        self.mock_db.get_match_weights.return_value = {'time': 0.2, 'place': 0.1, 'cost': 0.3, 'experience': 0.4}

        # Act: Call the worker's logic function directly
        self.service._execute_match_job_logic(self.job_id)

        # Assert:
        # 1. The database was queried for all necessary data.
        self.mock_db.execute_sql_file.assert_called_once_with('matching_query.sql', {'job_id': self.job_id})
        self.mock_db.find_job_by_id.assert_called_once_with(self.job_id)
        # 2. The final results were saved to the database.
        self.mock_db.save_matches.assert_called_once()
        # 3. The final results were stored in the cache.
        self.mock_cache.set.assert_called_once()

    def test_get_match_results_retrieves_from_cache(self):
        """
        Scenario: A client requests the results of a job.
        Expected: The service attempts to retrieve the results from the cache.
        """
        # Arrange
        cached_results = [{'user_id': 'user1', 'final_score': 0.9}]
        self.mock_cache.get.return_value = cached_results

        # Act
        results = self.service.get_match_results(self.job_id)

        # Assert
        self.assertEqual(results, cached_results)
        self.mock_cache.get.assert_called_once_with(f"matches:{self.job_id}")

if __name__ == '__main__':
    unittest.main()
