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
from auction.models import HiBidUpload
from threading import Thread, Event
from datetime import datetime
from django.utils import timezone
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
# from auction.scripts.upload_to_hibid import upload_to_hibid_main
from auction.models import Event
from auction.utils.redis_utils import RedisTaskStatus
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
            ending_date = datetime.strptime(request.POST.get('ending_date'), '%Y-%m-%d')
            selected_warehouse = request.POST.get('selected_warehouse')

            if not all([auction_title, ending_date, selected_warehouse]):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            config_manager.set_active_warehouse(selected_warehouse)

            task_id = f"create_auction_{int(time.time())}"
            RedisTaskStatus.set_status(task_id, "STARTED", f"Starting auction creation for {auction_title}")

            def run_async_task():
                asyncio.run(create_auction_main(
                    auction_title,
                    ending_date,
                    selected_warehouse,
                    task_id
                ))

            thread = Thread(target=run_async_task)
            thread.start()

            logger.info(f"Auction creation thread started for {auction_title}")
            return JsonResponse({'message': 'Auction creation process started', 'task_id': task_id})
        except ValueError as e:
            logger.error(f"Invalid date format: {str(e)}")
            return JsonResponse({'error': 'Invalid date format'}, status=400)
        except Exception as e:
            logger.error(f"Error starting auction creation task: {str(e)}")
            logger.error(traceback.format_exc())
            return JsonResponse({'error': 'Failed to start auction creation task', 'details': str(e)}, status=500)

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
            upload_choice = int(data.get('upload_choice'))

            today = timezone.now().date()
            event = Event.objects.filter(
                event_id=event_id,
                warehouse=warehouse,
                ending_date__lte=today
            ).first()

            if not event:
                return JsonResponse({'error': 'Invalid Auction ID or auction has not ended yet.'}, status=400)

            task_id = f"void_unpaid_{int(time.time())}"
            RedisTaskStatus.set_status(task_id, "STARTED", f"Starting void unpaid process for auction {event_id}")

            Thread(target=void_unpaid_main, kwargs={
                'event_id': event_id,
                'upload_choice': upload_choice,
                'warehouse': warehouse,
                'task_id': task_id
            }).start()

            return JsonResponse({'message': 'Void unpaid process started', 'task_id': task_id})

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.exception("Unexpected error in void_unpaid_view")
            return JsonResponse({'error': f'An unexpected error occurred: {str(e)}'}, status=500)

@login_required
def remove_duplicates_view(request):
    auctions = get_auction_numbers(request)
    warehouses = list(warehouse_data.keys())

    if request.method == 'POST':
        auction_number = request.POST.get('auction_number')
        target_msrp = float(request.POST.get('target_msrp'))
        warehouse_name = request.POST.get('warehouse_name')

        task_id = f"remove_duplicates_{int(time.time())}"
        RedisTaskStatus.set_status(task_id, "STARTED", f"Starting remove duplicates process for auction {auction_number}")

        Thread(target=remove_duplicates_main, kwargs={
            'auction_number': auction_number,
            'target_msrp': target_msrp,
            'warehouse_name': warehouse_name,
            'task_id': task_id
        }).start()

        return JsonResponse({'message': 'Remove duplicates process started', 'task_id': task_id})

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
        auction_id = request.POST.get('auction_id')
        selected_warehouse = request.POST.get('selected_warehouse')
        starting_price = request.POST.get('starting_price')  # Get the starting price

        config_manager.set_active_warehouse(selected_warehouse)

        should_stop = threading.Event()
        task_id = f"auction_formatter_{int(time.time())}"
        RedisTaskStatus.set_status(task_id, "STARTED", f"Starting auction formatter for auction {auction_id}")

        Thread(target=auction_formatter_main, kwargs={
            'auction_id': auction_id,
            'selected_warehouse': selected_warehouse,
            'starting_price': starting_price,  # Pass the starting price
            'gui_callback': logger.info,
            'should_stop': should_stop,
            'callback': lambda: None,
            'task_id': task_id
        }).start()

        return JsonResponse({'message': 'Auction formatter process started', 'task_id': task_id})

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
    }
    return render(request, 'auction/auction_formatter.html', context)

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
    logger.info(f"Checking status for task: {task_id}")
    try:
        status_data = RedisTaskStatus.get_status(task_id)
        logger.info(f"Status data for task {task_id}: {status_data}")
        if status_data:
            return JsonResponse(status_data)
        logger.warning(f"Task not found: {task_id}")
        return JsonResponse({'error': 'Task not found'}, status=404)
    except Exception as e:
        logger.error(f"Error checking task status: {str(e)}")
        logger.exception("Full traceback:")
        return JsonResponse({'error': 'Internal server error'}, status=500)
    