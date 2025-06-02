from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse, HttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils.encoding import smart_str
from django.conf import settings
import requests
import logging
import threading
import json
import traceback
import os
import asyncio
from auction.tasks import create_auction_task
from auction.models import HiBidUpload
from threading import Thread, Event
from datetime import datetime
from django.utils import timezone
from auction.utils import config_manager
from celery.backends.redis import RedisBackend
from auction_webapp.celery import app
from auction.scripts.create_auction import format_date, get_image, create_auction, save_event_to_database
from auction.scripts.create_auction import create_auction_task
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_task
from auction.scripts.auction_formatter import auction_formatter_task
# from auction.scripts.upload_to_hibid import upload_to_hibid_main
from auction.models import Event
from auction.utils.redis_utils import RedisTaskStatus
from celery.result import AsyncResult
from .tasks import auction_formatter_task, create_auction_task, remove_duplicates_task, void_unpaid_task
import time

logger = logging.getLogger(__name__)

# Load initial config
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'utils', 'config.json')

config_manager.load_config(config_path)
warehouse_data = config_manager.config.get('warehouses', {})

# Set a default warehouse if one exists
if warehouse_data:
    default_warehouse = next(iter(warehouse_data))
    config_manager.set_active_warehouse(default_warehouse)

@login_required
def home(request):
    warehouses = list(warehouse_data.keys())
    active_warehouse = config_manager.active_warehouse or (warehouses[0] if warehouses else None)
    
    # Get statistics for dashboard
    today = timezone.now().date()
    active_auctions = Event.objects.filter(ending_date__gte=today).count()
    completed_auctions = Event.objects.filter(ending_date__lt=today).count()
    
    context = {
        'warehouses': warehouses,
        'default_warehouse': active_warehouse,
        'active_auctions': active_auctions,
        'completed_auctions': completed_auctions,
    }
    return render(request, 'auction/home.html', context)

@login_required
def get_auction_numbers(request):
    try:
        events = Event.objects.all()
        logger.debug(f"Loaded {events.count()} events from database")
        
        auction_numbers = [
            {
                'id': event.event_id,
                'title': event.title,
                'timestamp': event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                'ending_date': event.ending_date.strftime("%Y-%m-%d %H:%M:%S"),
                'warehouse': event.warehouse
            }
            for event in events
        ]
        logger.debug(f"Processed auction numbers: {auction_numbers}")
        return auction_numbers
    except Exception as e:
        logger.error(f"Error in get_auction_numbers: {str(e)}")
        logger.exception("Full traceback:")
        return []
    
@login_required
def download_formatted_csv(request, auction_id):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    resources_dir = os.path.join(script_dir, 'resources', 'processed_csv')
    csv_path = os.path.join(resources_dir, f'{auction_id}.csv')

    logger.info(f"Attempting to download CSV from: {csv_path}")

    if os.path.exists(csv_path):
        logger.info(f"CSV file found at: {csv_path}")
        try:
            with open(csv_path, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{smart_str(auction_id)}.csv"'
                return response
        except IOError:
            logger.error(f"IOError when reading file: {csv_path}")
            return HttpResponse("Error reading the CSV file", status=500)
    else:
        logger.error(f"CSV file not found at: {csv_path}")
        return HttpResponse(f"CSV file not found at {csv_path}", status=404)

@login_required
def get_warehouse_events(request):
    warehouse = request.GET.get('warehouse')
    process_type = request.GET.get('process_type', 'future')
    logger.info(f"Fetching events for warehouse: {warehouse}, process_type: {process_type}")
    
    # Normalize warehouse name for comparison
    if warehouse:
        warehouse = warehouse.strip()
    
    all_events = Event.objects.filter(warehouse=warehouse)
    today = timezone.now().date()
    ten_days_ago = today - timezone.timedelta(days=10)
    
    # Add debug logging
    logger.info(f"Total events in database for {warehouse}: {all_events.count()}")
    logger.info(f"Today's date: {today}")
    
    for event in all_events[:5]:  # Log first 5 events only to avoid spam
        logger.info(f"Event: {event.event_id} - {event.title} - Ending: {event.ending_date} - Warehouse: '{event.warehouse}'")
    
    # Also check for events with similar warehouse names (case-insensitive)
    if all_events.count() == 0 and warehouse:
        similar_events = Event.objects.filter(warehouse__iexact=warehouse)
        if similar_events.count() > 0:
            logger.warning(f"Found {similar_events.count()} events with case-insensitive match for warehouse '{warehouse}'")
            all_events = similar_events

    if process_type == 'past':
        # For void_unpaid process - only show auctions that ended within the last 10 days
        filtered_events = [
            {
                'id': event.event_id,
                'title': event.title,
                'ending_date': event.ending_date.strftime("%Y-%m-%d"),
                'warehouse': event.warehouse
            }
            for event in all_events
            if event.ending_date < today and event.ending_date >= ten_days_ago
        ]
    else:
        # For other processes (auction creation, formatting, etc.)
        filtered_events = [
            {
                'id': event.event_id,
                'title': event.title,
                'ending_date': event.ending_date.strftime("%Y-%m-%d"),
                'warehouse': event.warehouse
            }
            for event in all_events
            if event.ending_date >= today
        ]

    logger.info(f"Filtered events count: {len(filtered_events)}")
    if len(filtered_events) > 0:
        logger.info(f"First filtered event: {filtered_events[0]}")
    
    return JsonResponse(filtered_events, safe=False)

@login_required
def create_auction_view(request):
    if request.method == 'POST':
        try:
            auction_title = request.POST.get('auction_title')
            ending_date = request.POST.get('ending_date')
            ending_time = request.POST.get('ending_time', '18:30')  # Default to 6:30 PM if not provided
            selected_warehouse = request.POST.get('selected_warehouse')

            if not all([auction_title, ending_date, selected_warehouse]):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            try:
                # Validate the date format
                datetime.strptime(ending_date, '%Y-%m-%d')
                # Validate the time format
                datetime.strptime(ending_time, '%H:%M')
            except ValueError:
                return JsonResponse({'error': 'Invalid date or time format. Use YYYY-MM-DD for date and HH:MM for time.'}, status=400)

            # Start the Celery task
            task = create_auction_task.delay(auction_title, ending_date, selected_warehouse, ending_time)

            logger.info(f"Auction creation task started for {auction_title}")
            return JsonResponse({
                'message': 'Auction creation process started',
                'task_id': task.id
            })
        except Exception as e:
            logger.error(f"Error starting auction creation task: {str(e)}")
            logger.error(traceback.format_exc())
            return JsonResponse({'error': str(e)}, status=500)

    warehouses = list(warehouse_data.keys())
    return render(request, 'auction/create_auction.html', {
        'warehouses': warehouses,
    })

@login_required
@require_http_methods(["GET", "POST"])
def void_unpaid_view(request):
    warehouses = list(warehouse_data.keys())
    default_warehouse = warehouses[0] if warehouses else None

    if request.method == 'GET':
        context = {
            'warehouses': warehouses,
            'default_warehouse': default_warehouse,
        }
        return render(request, 'auction/void_unpaid.html', context)

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            warehouse = data.get('warehouse')
            event_id = data.get('auction_id')
            upload_choice = int(data.get('upload_choice', 0))  # Default to 0 if not provided

            # Validate required fields
            if not all([warehouse, event_id]):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            # Validate the event exists and has ended
            today = timezone.now().date()
            event = Event.objects.filter(
                event_id=event_id,
                warehouse=warehouse,
                ending_date__lte=today
            ).first()

            if not event:
                return JsonResponse({
                    'error': 'Invalid Auction ID or auction has not ended yet.'
                }, status=400)

            # Start the Celery task
            task = void_unpaid_main.delay(
                event_id=event_id,
                upload_choice=upload_choice,
                warehouse=warehouse
            )

            logger.info(f"Void unpaid task started for event {event_id}")
            return JsonResponse({
                'status': 'success',
                'message': 'Void unpaid process started',
                'task_id': task.id
            })

        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            logger.exception("Unexpected error in void_unpaid_view")
            return JsonResponse({
                'error': str(e),
                'status': 'error'
            }, status=500)

@login_required
def remove_duplicates_view(request):
    auctions = get_auction_numbers(request)
    warehouses = list(warehouse_data.keys())

    if request.method == 'POST':
        try:
            auction_number = request.POST.get('auction_number')
            target_msrp_str = request.POST.get('target_msrp')
            warehouse_name = request.POST.get('warehouse_name')
            
            # Validate required fields
            if not all([auction_number, target_msrp_str, warehouse_name]):
                missing_fields = []
                if not auction_number:
                    missing_fields.append('auction_number')
                if not target_msrp_str:
                    missing_fields.append('target_msrp')
                if not warehouse_name:
                    missing_fields.append('warehouse_name')
                
                error_msg = f'Missing required fields: {", ".join(missing_fields)}'
                logger.error(f"Remove duplicates error: {error_msg}")
                return JsonResponse({'error': error_msg}, status=400)
            
            # Validate and convert target_msrp
            try:
                target_msrp = float(target_msrp_str)
                if target_msrp <= 0:
                    raise ValueError("Target MSRP must be greater than 0")
            except ValueError as e:
                error_msg = f'Invalid target MSRP: {str(e)}'
                logger.error(f"Remove duplicates error: {error_msg}")
                return JsonResponse({'error': error_msg}, status=400)

            # Start the Celery task
            task = remove_duplicates_task.delay(auction_number, target_msrp, warehouse_name)
            
            logger.info(f"Remove duplicates task started for auction {auction_number}")
            return JsonResponse({
                'message': 'Remove duplicates process started',
                'task_id': task.id
            })
            
        except Exception as e:
            logger.exception("Unexpected error in remove_duplicates_view")
            return JsonResponse({'error': str(e)}, status=500)

    context = {
        'auctions_json': json.dumps(auctions, cls=DjangoJSONEncoder),
        'warehouses': warehouses,
    }
    return render(request, 'auction/remove_duplicates.html', context)

@login_required
def auction_formatter_view(request):
    warehouses = list(config_manager.config.get('warehouses', {}).keys())
    auctions = get_auction_numbers(request)

    if request.method == 'POST':
        try:
            auction_id = request.POST.get('auction_id')
            selected_warehouse = request.POST.get('selected_warehouse')

            if not all([auction_id, selected_warehouse]):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            # Pass a default starting price since we now use dynamic pricing
            # This parameter is no longer used in the actual processing
            default_starting_price = 1.00

            task = auction_formatter_task.delay(auction_id, selected_warehouse, default_starting_price)
            
            logger.info(f"Auction formatter task started for auction {auction_id}")
            return JsonResponse({
                'message': 'Auction formatter process started',
                'task_id': task.id
            })
        except Exception as e:
            logger.exception(f"Error starting auction formatter task for auction {auction_id}")
            return JsonResponse({'error': str(e)}, status=500)

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
    }
    return render(request, 'auction/auction_formatter.html', context)

@login_required
def get_task_status(request, task_id):
    task = AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Pending...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'status': task.info.get('status', '')
        }
    else:
        response = {
            'state': task.state,
            'status': str(task.info),
        }
    return JsonResponse(response)

@login_required
@require_http_methods(["GET", "POST"])
def upload_to_hibid_view(request):
    warehouses = list(config_manager.config.get('warehouses', {}).keys())
    auctions = get_auction_numbers(request)

    if request.method == 'POST':
        event_id = request.POST.get('auction_id')
        
        logger.info(f"Received POST request - event_id: {event_id}")

        if not event_id:
            logger.error("Missing event_id")
            return JsonResponse({'status': 'error', 'message': "Please select an event."})

        # Construct the n8n endpoint URL with the event_id as a query parameter
        n8n_endpoint = f"{settings.N8N_HIBID_UPLOAD_ENDPOINT}?event_id={event_id}"

        try:
            logger.info(f"Sending request to n8n workflow - endpoint: {n8n_endpoint}")
            response = requests.post(n8n_endpoint)
            response.raise_for_status()
            logger.info(f"n8n workflow response: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            logger.error(f"Error sending request to n8n workflow: {str(e)}")
            return JsonResponse({'status': 'error', 'message': f"Failed to start HiBid upload process: {str(e)}"})

        # Create a HiBidUpload record
        try:
            event = Event.objects.get(event_id=event_id)
            HiBidUpload.objects.create(
                event=event,
                status='in_progress'
            )
            logger.info(f"Created HiBidUpload record for event {event_id}")
        except Event.DoesNotExist:
            logger.error(f"Event not found in the database - event_id: {event_id}")
            return JsonResponse({'status': 'error', 'message': "Event not found in the database."})

        return JsonResponse({'status': 'success', 'message': "HiBid upload process started successfully."})

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
    }
    return render(request, 'auction/upload_to_hibid.html', context)

@login_required
def check_task_status(request, task_id):
    task = AsyncResult(task_id)
    response = {
        'state': task.state,
        'status': task.state,  # We'll use this for consistency with the frontend
        'message': '',
        'result': None
    }
    
    if task.state == 'PENDING':
        response['message'] = 'Task is waiting for execution'
    elif task.state == 'STARTED':
        response['message'] = 'Task has been started'
    elif task.state == 'SUCCESS':
        response['message'] = 'Task completed successfully'
        response['result'] = str(task.result)
    elif task.state == 'FAILURE':
        response['message'] = 'Task failed'
        response['result'] = str(task.result)
    else:
        response['message'] = 'Task is in progress'
        if isinstance(task.info, dict):
            response['message'] = task.info.get('status', '')
            response['result'] = task.info.get('result', '')
        else:
            response['message'] = str(task.info)

    return JsonResponse(response)

# Add a debug endpoint to check all events
@login_required
def debug_events(request):
    """Debug endpoint to check all events in the database"""
    all_events = Event.objects.all().order_by('-timestamp')
    events_data = []
    
    for event in all_events:
        events_data.append({
            'id': event.event_id,
            'title': event.title,
            'warehouse': event.warehouse,
            'start_date': event.start_date.strftime("%Y-%m-%d"),
            'ending_date': event.ending_date.strftime("%Y-%m-%d"),
            'timestamp': event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            'is_active': event.is_active()
        })
    
    return JsonResponse({
        'total_events': all_events.count(),
        'events': events_data,
        'current_date': timezone.now().date().strftime("%Y-%m-%d"),
        'warehouses': list(warehouse_data.keys())
    })

# Test endpoint to check warehouse events without UI
@login_required
def test_warehouse_events(request):
    """Test endpoint to debug warehouse event filtering"""
    warehouse = request.GET.get('warehouse', 'Maule Warehouse')
    
    # Get all events for debugging
    all_events = Event.objects.filter(warehouse=warehouse)
    today = timezone.now().date()
    
    response_data = {
        'warehouse': warehouse,
        'today': today.strftime("%Y-%m-%d"),
        'all_events_count': all_events.count(),
        'all_events': [],
        'future_events': [],
        'past_events': []
    }
    
    for event in all_events:
        event_data = {
            'id': event.event_id,
            'title': event.title,
            'ending_date': event.ending_date.strftime("%Y-%m-%d"),
            'days_until_end': (event.ending_date - today).days,
            'is_future': event.ending_date >= today
        }
        response_data['all_events'].append(event_data)
        
        if event.ending_date >= today:
            response_data['future_events'].append(event_data)
        else:
            response_data['past_events'].append(event_data)
    
    return JsonResponse(response_data)
    
    