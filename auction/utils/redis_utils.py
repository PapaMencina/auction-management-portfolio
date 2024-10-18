import json
import time
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class RedisTaskStatus:
    @staticmethod
    def set_status(task_id, status, message, progress=None):
        try:
            data = {
                'status': status,
                'message': message,
                'timestamp': int(time.time())
            }
            if progress is not None:
                data['progress'] = progress
            settings.REDIS_CONN.setex(f"task:{task_id}", 86400, json.dumps(data))  # Expire after 24 hours
            logger.info(f"Successfully set status for task {task_id}: {status}, progress: {progress}")
        except Exception as e:
            logger.error(f"Error setting Redis status for task {task_id}: {e}")
            logger.exception("Full traceback:")

    @staticmethod
    def get_status(task_id):
        try:
            data = settings.REDIS_CONN.get(f"task:{task_id}")
            if data:
                logger.info(f"Successfully retrieved status for task {task_id}")
                return json.loads(data)
            logger.warning(f"No status found for task {task_id}")
            return None
        except Exception as e:
            logger.error(f"Error getting Redis status for task {task_id}: {e}")
            logger.exception("Full traceback:")
            return None

    @staticmethod
    def test_connection():
        try:
            settings.REDIS_CONN.ping()
            logger.info("Redis connection test successful")
            return True
        except Exception as e:
            logger.error(f"Redis connection test failed: {e}")
            logger.exception("Full traceback:")
            return False