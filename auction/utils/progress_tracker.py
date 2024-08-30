import json
from django.core.cache import cache
from functools import wraps
import logging
import traceback
import time

logger = logging.getLogger(__name__)

class ProgressTracker:
    @staticmethod
    def update_progress(task_id, progress, status):
        logger.info(f"Updating progress for task {task_id}: {progress}% - {status}")
        cache_key = f"task_progress_{task_id}"
        cache_value = json.dumps({'progress': progress, 'status': status, 'timestamp': time.time()})
        cache.set(cache_key, cache_value, timeout=3600)  # 1 hour timeout

    @staticmethod
    def get_progress(task_id):
        logger.info(f"Getting progress for task {task_id}")
        cache_key = f"task_progress_{task_id}"
        retry_count = 0
        max_retries = 5
        retry_delay = 0.1

        while retry_count < max_retries:
            cache_value = cache.get(cache_key)
            if cache_value:
                try:
                    progress_data = json.loads(cache_value)
                    logger.info(f"Progress data found for task {task_id}: {progress_data}")
                    return progress_data
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON for task {task_id}: {cache_value}")
            else:
                logger.warning(f"No progress data found for task {task_id}, retry {retry_count + 1}")
                time.sleep(retry_delay)
                retry_count += 1
                retry_delay *= 2  # Exponential backoff

        logger.error(f"Failed to retrieve progress data for task {task_id} after {max_retries} attempts")
        return {'progress': 0, 'status': 'Task not found or completed', 'timestamp': time.time()}

def with_progress_tracking(func):
    @wraps(func)
    def wrapper(task_id, *args, **kwargs):
        def update_progress(progress, status):
            ProgressTracker.update_progress(task_id, progress, status)

        try:
            return func(*args, update_progress=update_progress, **kwargs)
        except Exception as e:
            logger.error(f"Error in task {task_id}: {str(e)}")
            logger.error(traceback.format_exc())
            ProgressTracker.update_progress(task_id, 100, f"Error: {str(e)}")
            raise
    return wrapper