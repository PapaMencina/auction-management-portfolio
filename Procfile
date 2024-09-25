release: python manage.py migrate
web: python manage.py collectstatic --noinput && gunicorn auction_webapp.wsgi