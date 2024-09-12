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
from threading import Thread, Event
from datetime import datetime, timezone
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
from auction.scripts.upload_to_hibid import upload_to_hibid_main
from uuid import uuid4
from auction.utils.progress_tracker import ProgressTracker, with_progress_tracking


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
def load_events(request):
    try:
        # Use Django's settings to get the base directory of your project
        base_dir = settings.BASE_DIR

        # Construct the path to events.json relative to your project root
        file_path = os.path.join(base_dir, 'auction', 'resources', 'events.json')

        logger.info(f"Attempting to read events.json from: {file_path}")

        if os.path.exists(file_path):
            logger.info(f"events.json file found at {file_path}")
            with open(file_path, "r") as file:
                events = json.load(file)
            logger.info(f"Loaded {len(events)} events")
            return events
        else:
            logger.warning(f"Events file not found at {file_path}")
            return []
    except Exception as e:
        logger.error(f"Error loading events: {str(e)}")
        logger.exception("Full traceback:")
        return []

@login_required
def get_auction_numbers(request):
    try:
        events = load_events(request)
        logger.debug(f"Loaded events: {events}")
        auction_numbers = [
            {
                'id': event.get('event_id') or event.get('id'),  # Use 'id' if 'event_id' is not present
                'title': event['title'],
                'timestamp': event['timestamp'],
                'ending_date': event.get('ending_date'),
                'warehouse': event['warehouse']
            }
            for event in events
        ]
        logger.debug(f"Processed auction numbers: {auction_numbers}")
        return auction_numbers
    except Exception as e:
        logger.error(f"Error in get_auction_numbers: {str(e)}")
        logger.exception("Full traceback:")
        return []

# Load initial config
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'utils', 'config.json')

config_manager.load_config(config_path)
warehouse_data = config_manager.config.get('warehouses', {})

@login_required
def get_warehouse_events(request):
    warehouse = request.GET.get('warehouse')
    all_events = get_auction_numbers(request)
    filtered_events = [event for event in all_events if event['warehouse'] == warehouse]
    return JsonResponse(filtered_events, safe=False)

@login_required
def select_warehouse(request):
    if request.method == 'POST':
        selected_warehouse = request.POST.get('warehouse')
        if selected_warehouse in warehouse_data:
            config_manager.set_active_warehouse(selected_warehouse)
            request.session['selected_warehouse'] = selected_warehouse
            messages.success(request, f"Warehouse {selected_warehouse} selected and configuration loaded.")
        else:
            messages.error(request, "Invalid warehouse selection.")
    return redirect('home')

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

            task_id = str(uuid4())
            logger.info(f"Creating auction task with ID: {task_id}")

            ProgressTracker.update_progress(task_id, 0, "Task started")

            thread = Thread(target=create_auction_main, args=(
                task_id,
                auction_title,
                ending_date,
                show_browser,
                selected_warehouse
            ))
            thread.start()

            logger.info(f"Auction creation thread started for task {task_id}")
            return JsonResponse({'task_id': task_id})
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

def get_task_progress(request, task_id):
    progress = ProgressTracker.get_progress(task_id)
    if progress is None:
        return JsonResponse({'error': 'Task not found', 'status': 'Not Found'}, status=404)
    return JsonResponse(progress)

@login_required
@require_http_methods(["GET", "POST"])
def void_unpaid_view(request):
    warehouses = list(warehouse_data.keys())
    default_warehouse = warehouses[0] if warehouses else None
    events = load_events(request)
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
            auction_id = data.get('auction_id')
            upload_choice = int(data.get('upload_choice'))
            show_browser = data.get('show_browser') and can_use_show_browser

            # Validate auction ID against the selected warehouse
            valid_auction = any(event for event in events if event['event_id'] == auction_id and event['warehouse'] == warehouse)

            if not valid_auction:
                return JsonResponse({'error': 'Invalid Auction ID - Please confirm the auction ID and Warehouse, then try again.'}, status=400)

            task_id = str(uuid4())

            Thread(target=void_unpaid_main, kwargs={
                'auction_id': auction_id,
                'upload_choice': upload_choice,
                'show_browser': show_browser,
                'warehouse': warehouse,
                'task_id': task_id
            }).start()

            return JsonResponse({'task_id': task_id})

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

        task_id = str(uuid4())

        Thread(target=remove_duplicates_main, kwargs={
            'auction_number': auction_number,
            'target_msrp': target_msrp,
            'warehouse_name': warehouse_name,
            'task_id': task_id
        }).start()

        return JsonResponse({'task_id': task_id})

    context = {
        'auctions_json': json.dumps([{
            'id': event.get('event_id') or event.get('id'),  # Use 'id' if 'event_id' is not present
            'warehouse': event['warehouse'],
            'title': event['title'],
            'timestamp': event['timestamp']
        } for event in auctions], cls=DjangoJSONEncoder),
        'warehouses': warehouses,
    }
    return render(request, 'auction/remove_duplicates.html', context)

@login_required
def auction_formatter_view(request):
    warehouses = list(config_manager.config.get('warehouses', {}).keys())
    auctions = get_auction_numbers(request)
    can_use_show_browser = request.user.has_perm('auction.can_use_show_browser')

    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        selected_warehouse = request.POST.get('selected_warehouse')
        show_browser = request.POST.get('show_browser') == '1' and can_use_show_browser

        config_manager.set_active_warehouse(selected_warehouse)

        task_id = str(uuid4())

        def gui_callback(message):
            ProgressTracker.update_progress(task_id, 0, message)

        should_stop = threading.Event()

        Thread(target=auction_formatter_main, args=(task_id,), kwargs={
            'auction_id': auction_id,
            'selected_warehouse': selected_warehouse,
            'gui_callback': gui_callback,
            'should_stop': should_stop,
            'callback': lambda: None,
            'show_browser': show_browser
        }).start()

        return JsonResponse({'task_id': task_id})

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
        'can_use_show_browser': can_use_show_browser,
    }
    return render(request, 'auction/auction_formatter.html', context)

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

        ending_date_str = selected_auction.get('ending_date') or selected_auction.get('timestamp')

        if not ending_date_str:
            return JsonResponse({'status': 'error', 'message': "No valid ending date found for the selected auction."})

        try:
            ending_date = datetime.fromisoformat(ending_date_str.rstrip('Z')).replace(tzinfo=timezone.utc)
            ending_date_str = ending_date.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, AttributeError) as e:
            return JsonResponse({'status': 'error', 'message': "Invalid date format in auction data."})

        auction_title = selected_auction['title']

        config_manager.set_active_warehouse(selected_warehouse)

        task_id = str(uuid4())
        should_stop = threading.Event()

        def gui_callback(message):
            ProgressTracker.update_progress(task_id, 0, message)

        Thread(target=upload_to_hibid_main, kwargs={
            'auction_id': auction_id,
            'ending_date': ending_date_str,
            'auction_title': auction_title,
            'gui_callback': gui_callback,
            'should_stop': should_stop,
            'callback': lambda: None,
            'show_browser': show_browser,
            'selected_warehouse': selected_warehouse,
            'task_id': task_id
        }).start()

        return JsonResponse({'task_id': task_id})

    # GET request handling
    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
        'can_use_show_browser': can_use_show_browser,
    }
    return render(request, 'auction/upload_to_hibid.html', context)
