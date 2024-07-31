from django.urls import path
from . import views

app_name = 'auction'  # Add this line to create a namespace

urlpatterns = [
    path('', views.home, name='home'),
    path('select_warehouse/', views.select_warehouse, name='select_warehouse'),
    path('create/', views.create_auction_view, name='create_auction'),
    path('void_unpaid/', views.void_unpaid_view, name='void_unpaid'),
    path('remove_duplicates/', views.remove_duplicates_view, name='remove_duplicates'),
    path('auction_formatter/', views.auction_formatter_view, name='auction_formatter'),
    path('upload_to_hibid/', views.upload_to_hibid_view, name='upload_to_hibid'),
]