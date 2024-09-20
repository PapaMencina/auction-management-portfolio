from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse, HttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils.encoding import smart_str
from django.conf import settings
import logging
import threading
import json
import traceback
import os
import asyncio
from threading import Thread, Event
from datetime import datetime, timezone
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
from auction.scripts.upload_to_hibid import upload_to_hibid_main
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
def get_warehouse_events(request):
    warehouse = request.GET.get('warehouse')
    all_events = get_auction_numbers(request)
    filtered_events = [event for event in all_events if event['warehouse'] == warehouse]
    return JsonResponse(filtered_events, safe=False)

@login_required
def create_auction_view(request):
    if request.method == 'POST':
        try:
            auction_title = request.POST.get('auction_title')
            ending_date = datetime.strptime(request.POST.get('ending_date'), '%Y-%m-%d')
            show_browser = 'show_browser' in request.POST and request.user.has_perm('auction.can_use_show_browser')
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
                    show_browser,
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
    can_use_show_browser = request.user.has_perm('auction.can_use_show_browser')
    return render(request, 'auction/create_auction.html', {
        'warehouses': warehouses,
        'can_use_show_browser': can_use_show_browser
    })

@login_required
@require_http_methods(["GET", "POST"])
def void_unpaid_view(request):
    warehouses = list(warehouse_data.keys())
    default_warehouse = warehouses[0] if warehouses else None
    auctions = get_auction_numbers(request)
    can_use_show_browser = request.user.has_perm('auction.can_use_show_browser')

    if request.method == 'GET':
        context = {
            'warehouses': warehouses,
            'default_warehouse': default_warehouse,
            'can_use_show_browser': can_use_show_browser,
        }
        return render(request, 'auction/void_unpaid.html', context)

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            warehouse = data.get('warehouse')
            event_id = data.get('auction_id')
            upload_choice = int(data.get('upload_choice'))
            show_browser = data.get('show_browser') and can_use_show_browser

            valid_auction = any(auction for auction in auctions if auction['id'] == event_id and auction['warehouse'] == warehouse)

            if not valid_auction:
                return JsonResponse({'error': 'Invalid Auction ID - Please confirm the auction ID and Warehouse, then try again.'}, status=400)

            task_id = f"void_unpaid_{int(time.time())}"
            RedisTaskStatus.set_status(task_id, "STARTED", f"Starting void unpaid process for auction {event_id}")

            Thread(target=void_unpaid_main, kwargs={
            'event_id': event_id,
            'upload_choice': upload_choice,
            'warehouse': warehouse,
            'task_id': task_id  # Add this line
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

        config_manager.set_active_warehouse(selected_warehouse)

        should_stop = threading.Event()
        task_id = f"auction_formatter_{int(time.time())}"
        RedisTaskStatus.set_status(task_id, "STARTED", f"Starting auction formatter for auction {auction_id}")

        Thread(target=auction_formatter_main, kwargs={
            'auction_id': auction_id,
            'selected_warehouse': selected_warehouse,
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
    warehouses = list(warehouse_data.keys())
    auctions = get_auction_numbers(request)
    can_use_show_browser = request.user.has_perm('auction.can_use_show_browser')

    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        show_browser = 'show_browser' in request.POST and can_use_show_browser
        selected_warehouse = request.POST.get('selected_warehouse')

        if not all([auction_id, selected_warehouse]):
            return JsonResponse({'status': 'error', 'message': "Please select both a warehouse and an event."})

        selected_auction = next((a for a in auctions if a['id'] == auction_id), None)

        if not selected_auction:
            return JsonResponse({'status': 'error', 'message': "Invalid auction selected."})

        ending_date_str = selected_auction.get('ending_date')

        if not ending_date_str:
            return JsonResponse({'status': 'error', 'message': "No valid ending date found for the selected auction."})

        try:
            ending_date = datetime.strptime(ending_date_str, '%Y-%m-%d %H:%M:%S')
            ending_date_str = ending_date.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError as e:
            return JsonResponse({'status': 'error', 'message': "Invalid date format in auction data."})

        auction_title = selected_auction['title']

        config_manager.set_active_warehouse(selected_warehouse)

        should_stop = threading.Event()
        task_id = f"upload_to_hibid_{int(time.time())}"
        RedisTaskStatus.set_status(task_id, "STARTED", f"Starting HiBid upload for auction {auction_id}")

        Thread(target=upload_to_hibid_main, kwargs={
            'auction_id': auction_id,
            'ending_date': ending_date_str,
            'auction_title': auction_title,
            'gui_callback': logger.info,
            'should_stop': should_stop,
            'callback': lambda: None,
            'show_browser': show_browser,
            'selected_warehouse': selected_warehouse,
            'task_id': task_id
        }).start()

        return JsonResponse({'message': 'Upload to HiBid process started', 'task_id': task_id})

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
        'can_use_show_browser': can_use_show_browser,
    }
    return render(request, 'auction/upload_to_hibid.html', context)

@login_required
def check_task_status(request, task_id):
    try:
        status_data = RedisTaskStatus.get_status(task_id)
        if status_data:
            return JsonResponse(status_data)
        return JsonResponse({'error': 'Task not found'}, status=404)
    except Exception as e:
        logger.error(f"Error checking task status: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)