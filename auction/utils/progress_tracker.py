import threading
import json
from django.core.cache import cache

class ProgressTracker:
    def __init__(self, task_id):
        self.task_id = task_id
        self.progress = 0
        self.status = "Starting..."
        self._update_cache()
    
    @staticmethod
    def get_progress(task_id):
        progress_data = cache.get(f"task_progress_{task_id}")
        if progress_data:
            return json.loads(progress_data)
        return {'progress': 0, 'status': 'Task not found or completed'}

    def update(self, progress, status):
        self.progress = progress
        self.status = status
        self._update_cache()

    def set_error(self, error_message):
        cache.set(f"task_progress_{self.task_id}", json.dumps({
            'error': error_message
        }), timeout=3600)  # Cache for 1 hour

    def _update_cache(self):
        cache.set(f"task_progress_{self.task_id}", json.dumps({
            'progress': self.progress,
            'status': self.status
        }), timeout=3600)  # Cache for 1 hour

def run_with_progress(func):
    def wrapper(*args, **kwargs):
        task_id = kwargs.pop('task_id')
        progress_tracker = ProgressTracker(task_id)
        
        def update_progress(progress, status):
            progress_tracker.update(progress, status)
        
        try:
            thread = threading.Thread(target=func, args=args, kwargs={**kwargs, 'update_progress': update_progress})
            thread.start()
            return task_id
        except Exception as e:
            progress_tracker.set_error(str(e))
            raise
    return wrapper