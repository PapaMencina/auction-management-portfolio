# auction/urls.py
from django.urls import path
from . import views

app_name = 'auction'

urlpatterns = [
    path('', views.home, name='home'),
    path('check-task-status/<str:task_id>/', views.check_task_status, name='check_task_status'),
    path('get-task-status/<str:task_id>/', views.get_task_status, name='get_task_status'),
    path('create-auction/', views.create_auction_view, name='create_auction'),
    path('void-unpaid/', views.void_unpaid_view, name='void_unpaid'),
    path('remove-duplicates/', views.remove_duplicates_view, name='remove_duplicates'),
    path('format-auction/', views.auction_formatter_view, name='auction_formatter'),
    path('upload-to-hibid/', views.upload_to_hibid_view, name='upload_to_hibid'),
    path('download-csv/<str:auction_id>/', views.download_formatted_csv, name='download_formatted_csv'),
    path('get-warehouse-events/', views.get_warehouse_events, name='get_warehouse_events'),
    path('debug-events/', views.debug_events, name='debug_events'),
    path('test-warehouse-events/', views.test_warehouse_events, name='test_warehouse_events'),
]