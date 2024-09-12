import json
from django.core.cache import cache
from functools import wraps
import logging
import traceback
import time
import threading
from auction.models import TaskProgress
from django.utils import timezone

logger = logging.getLogger(__name__)

class ProgressTracker:
    @staticmethod
    def update_progress(task_id, progress, status, error=None):
        TaskProgress.objects.update_or_create(
            task_id=task_id,
            defaults={
                'progress': progress,
                'status': status,
                'error': error,
                'timestamp': timezone.now()
            }
        )

    @staticmethod
    def get_progress(task_id):
        try:
            task = TaskProgress.objects.get(task_id=task_id)
            return {
                'progress': task.progress,
                'status': task.status,
                'error': task.error,
                'timestamp': task.timestamp.timestamp()
            }
        except TaskProgress.DoesNotExist:
            return None

def with_progress_tracking(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        task_id = kwargs.get('task_id')
        if not task_id:
            task_id = threading.get_ident()
        
        def update_progress(progress, status):
            ProgressTracker.update_progress(task_id, progress, status)

        try:
            kwargs['update_progress'] = update_progress
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in task {task_id}: {str(e)}")
            logger.error(traceback.format_exc())
            ProgressTracker.update_progress(task_id, 100, f"Error: {str(e)}")
            raise

    return wrapper

class SharedEvents:
    def __init__(self):
        self.events = []

    def add_event(self, title, event_id, ending_date, timestamp):
        self.events.append({
            "title": title,
            "event_id": event_id,
            "ending_date": str(ending_date),
            "timestamp": timestamp
        })
        logger.info(f"Event added: {title}, ID: {event_id}, Ending Date: {ending_date}, Timestamp: {timestamp}")

shared_events = SharedEvents()