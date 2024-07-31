from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect

def redirect_to_login_or_home(request):
    if request.user.is_authenticated:
        return redirect('auction:home')
    return redirect('login')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', redirect_to_login_or_home, name='root'),
    path('auction/', include('auction.urls', namespace='auction')),
]