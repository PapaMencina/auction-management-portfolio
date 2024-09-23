"""
WSGI config for auction_webapp project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application
from dynoscale.wsgi import DynoscaleWsgiApp

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'auction_webapp.settings')

application = get_wsgi_application()

# Wrap the WSGI application with Dynoscale
application = DynoscaleWsgiApp(application)