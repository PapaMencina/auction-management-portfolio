# auction_webapp/urls.py
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', login_required(lambda request: redirect('auction:home')), name='home'),
    path('auction/', include(('auction.urls', 'auction'), namespace='auction')),  # Ensure namespace is correctly used
    path('accounts/', include('django.contrib.auth.urls')),  # Include default auth URLs
]
