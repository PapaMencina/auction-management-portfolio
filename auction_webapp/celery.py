# auction_webapp/celery.py
import os
from celery import Celery
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'auction_webapp.settings')

app = Celery('auction_webapp')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

if settings.REDIS_URL.startswith('rediss://'):
    app.conf.broker_use_ssl = {
        'ssl_cert_reqs': None,
    }
    app.conf.redis_backend_use_ssl = {
        'ssl_cert_reqs': None,
    }

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')