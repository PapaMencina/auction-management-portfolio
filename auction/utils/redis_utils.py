import json
import time
from django.conf import settings

class RedisTaskStatus:
    @staticmethod
    def set_status(task_id, status, message):
        try:
            data = json.dumps({
                'status': status,
                'message': message,
                'timestamp': int(time.time())
            })
            settings.REDIS_CONN.setex(f"task:{task_id}", 86400, data)  # Expire after 24 hours
        except Exception as e:
            print(f"Error setting Redis status: {e}")

    @staticmethod
    def get_status(task_id):
        try:
            data = settings.REDIS_CONN.get(f"task:{task_id}")
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            print(f"Error getting Redis status: {e}")
            return None