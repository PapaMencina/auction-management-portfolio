release: python manage.py migrate && python manage.py collectstatic --noinput
web: gunicorn auction_webapp.wsgi
worker: celery -A auction_webapp worker --concurrency=6 --loglevel=info