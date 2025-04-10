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
    context = {
        'warehouses': warehouses,
        'default_warehouse': active_warehouse,
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
    
    all_events = Event.objects.filter(warehouse=warehouse)
    today = timezone.now().date()

    if process_type == 'past':
        # For void_unpaid process
        filtered_events = [
            {
                'id': event.event_id,
                'title': event.title,
                'ending_date': event.ending_date.strftime("%Y-%m-%d %H:%M:%S"),
                'warehouse': event.warehouse
            }
            for event in all_events
            if event.ending_date < today
        ]
    else:
        # For other processes (auction creation, formatting, etc.)
        filtered_events = [
            {
                'id': event.event_id,
                'title': event.title,
                'ending_date': event.ending_date.strftime("%Y-%m-%d %H:%M:%S"),
                'warehouse': event.warehouse
            }
            for event in all_events
            if event.ending_date >= today
        ]

    logger.info(f"Filtered events count: {len(filtered_events)}")
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
        auction_number = request.POST.get('auction_number')
        target_msrp = float(request.POST.get('target_msrp'))
        warehouse_name = request.POST.get('warehouse_name')

        # Start the Celery task
        task = remove_duplicates_task.delay(auction_number, target_msrp, warehouse_name)

        return JsonResponse({'message': 'Remove duplicates process started', 'task_id': task.id})

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
            starting_price = request.POST.get('starting_price')

            if not all([auction_id, selected_warehouse, starting_price]):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            try:
                starting_price = float(starting_price)
                if starting_price <= 0:
                    raise ValueError("Starting price must be greater than 0")
            except ValueError as e:
                return JsonResponse({'error': f'Invalid starting price: {str(e)}'}, status=400)

            task = auction_formatter_task.delay(auction_id, selected_warehouse, starting_price)
            
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
    
    