import json
import time
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class RedisTaskStatus:
    # Status constants
    STATUS_NOT_STARTED = "NOT_STARTED"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_ERROR = "ERROR"
    STATUS_WARNING = "WARNING"

    @staticmethod
    def set_status(task_id, status, message, progress=None, stage=None, substage=None, error_context=None):
        try:
            data = {
                'status': status,
                'message': message,
                'timestamp': int(time.time()),
                'stage': stage,
                'substage': substage,
                'error_context': error_context
            }
            if progress is not None:
                data['progress'] = progress
            
            # Store history of status updates
            history_key = f"task_history:{task_id}"
            history_entry = {
                'timestamp': data['timestamp'],
                'status': status,
                'message': message,
                'stage': stage,
                'substage': substage
            }
            
            # Add to history list
            try:
                settings.REDIS_CONN.rpush(history_key, json.dumps(history_entry))
                # Keep only last 50 entries
                settings.REDIS_CONN.ltrim(history_key, -50, -1)
                # Set expiry on history
                settings.REDIS_CONN.expire(history_key, 86400)  # 24 hours
            except Exception as he:
                logger.warning(f"Failed to update history for task {task_id}: {he}")

            settings.REDIS_CONN.setex(f"task:{task_id}", 86400, json.dumps(data))  # Expire after 24 hours
            logger.info(f"Task {task_id} status update: {status} at stage: {stage}, substage: {substage}")
        except Exception as e:
            logger.error(f"Error setting Redis status for task {task_id}: {e}")
            logger.exception("Full traceback:")

    @staticmethod
    def get_status(task_id, include_history=False):
        try:
            data = settings.REDIS_CONN.get(f"task:{task_id}")
            result = json.loads(data) if data else None
            
            if include_history:
                history = []
                history_key = f"task_history:{task_id}"
                try:
                    history_data = settings.REDIS_CONN.lrange(history_key, 0, -1)
                    history = [json.loads(entry) for entry in history_data]
                except Exception as he:
                    logger.warning(f"Failed to get history for task {task_id}: {he}")
                
                if result:
                    result['history'] = history

            return result
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