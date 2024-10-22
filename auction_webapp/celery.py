import os
import asyncio
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

# Configure SSL settings for Redis if using SSL
if settings.REDIS_URL.startswith('rediss://'):
    ssl_config = {
        'ssl_cert_reqs': None,
        'ssl_ca_certs': None,
        'ssl_keyfile': None,
        'ssl_certfile': None
    }
    app.conf.update(
        broker_use_ssl=ssl_config,
        redis_backend_use_ssl=ssl_config,
        broker_connection_retry_on_startup=True
    )
    # Ensure broker pool settings are also updated
    app.conf.broker_pool_limit = 3  # Limit connection pool size

# Set the default event loop policy to use
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')