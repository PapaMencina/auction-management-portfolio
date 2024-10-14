release: python manage.py migrate
web: python manage.py collectstatic --noinput && gunicorn auction_webapp.wsgi
worker: celery -A auction_webapp worker --loglevel=info