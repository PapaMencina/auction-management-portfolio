import json
from django.core.cache import cache
from functools import wraps

class ProgressTracker:
    @staticmethod
    def update_progress(task_id, progress, status):
        cache.set(f"task_progress_{task_id}", json.dumps({
            'progress': progress,
            'status': status
        }), timeout=3600)

    @staticmethod
    def get_progress(task_id):
        progress_data = cache.get(f"task_progress_{task_id}")
        if progress_data:
            return json.loads(progress_data)
        return {'progress': 0, 'status': 'Task not found or completed'}

def with_progress_tracking(func):
    @wraps(func)
    def wrapper(task_id, *args, **kwargs):
        def update_progress(progress, status):
            ProgressTracker.update_progress(task_id, progress, status)
        
        return func(*args, update_progress=update_progress, **kwargs)
    return wrapper