# This file simulates a simple, in-memory job queue for background processing.
# In a real-world application, this would be replaced with a robust system
# like RabbitMQ, Celery, or Redis Queue.

import queue
import threading
import time
import logging

class JobQueue:
    """
    A simple in-memory job queue that uses a background thread to process tasks.
    """
    def __init__(self, worker_function):
        self.q = queue.Queue()
        self.worker_function = worker_function

        # Start the background worker thread
        worker_thread = threading.Thread(target=self._worker, daemon=True)
        worker_thread.start()
        logging.info("Job queue worker thread started.")

    def _worker(self):
        """
        The main loop for the background worker thread.
        It continuously fetches jobs from the queue and executes them.
        """
        while True:
            try:
                job_id = self.q.get()
                logging.info(f"Worker picked up job: {job_id}")
                self.worker_function(job_id)
                self.q.task_done()
                logging.info(f"Worker finished job: {job_id}")
            except Exception as e:
                logging.error(f"Error processing job {job_id}: {e}", exc_info=True)

    def enqueue_job(self, job_id):
        """
        Adds a new job to the queue for asynchronous processing.
        """
        logging.info(f"Enqueuing job: {job_id}")
        self.q.put(job_id)
        return {"status": "enqueued", "job_id": job_id}

# Global instance of the job queue
# This would be managed more formally in a larger application.
job_queue_instance = None

def initialize_job_queue(worker_function):
    """
    Initializes the global job queue with a specific worker function.
    """
    global job_queue_instance
    if job_queue_instance is None:
        job_queue_instance = JobQueue(worker_function)
    return job_queue_instance

def get_job_queue():
    """
    Returns the global job queue instance.
    """
    if job_queue_instance is None:
        raise Exception("Job queue has not been initialized.")
    return job_queue_instance
