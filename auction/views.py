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
import os
from threading import Thread, Event
from datetime import datetime, timezone
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
from auction.scripts.upload_to_hibid import upload_to_hibid_main

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
                'id': event['event_id'],
                'title': event['title'],
                'timestamp': event['timestamp'],
                'ending_date': event.get('ending_date'),  # Include ending_date if available
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
        auction_title = request.POST.get('auction_title')
        ending_date = datetime.strptime(request.POST.get('ending_date'), '%Y-%m-%d')
        show_browser = 'show_browser' in request.POST and request.user.has_perm('auction.can_use_show_browser')
        selected_warehouse = request.POST.get('selected_warehouse')

        config_manager.set_active_warehouse(selected_warehouse)
        create_auction_main(auction_title, ending_date, show_browser, selected_warehouse)
        result = f"Auction '{auction_title}' created successfully."
        return render(request, 'auction/result.html', {'result': result})
    
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
        logger.info("Received POST request to void_unpaid_view")
        logger.info(f"Request headers: {request.headers}")

        try:
            body = request.body.decode('utf-8')
            logger.info(f"Request body: {body}")
            
            data = json.loads(body)
            logger.info(f"Parsed JSON data: {data}")
            
            warehouse = data.get('warehouse')
            auction_id = data.get('auction_id')
            upload_choice = data.get('upload_choice')
            show_browser = data.get('show_browser') and can_use_show_browser
            
            logger.info(f"warehouse: {warehouse}")
            logger.info(f"auction_id: {auction_id}")
            logger.info(f"upload_choice: {upload_choice}")
            logger.info(f"show_browser: {show_browser}")

            # Check for missing parameters
            required_params = ['warehouse', 'auction_id', 'upload_choice']
            missing = [param for param in required_params if data.get(param) is None]
            if missing:
                return JsonResponse({'error': f'Missing required parameters: {", ".join(missing)}'}, status=400)

            # Convert types
            upload_choice = int(upload_choice)
            show_browser = bool(show_browser)

            # Validate auction ID against the selected warehouse
            valid_auction = any(event for event in events if event['event_id'] == auction_id and event['warehouse'] == warehouse)

            if not valid_auction:
                return JsonResponse({'error': 'Invalid Auction ID - Please confirm the auction ID and Warehouse, then try again.'}, status=400)

            # Call the main function with the warehouse parameter
            config_manager.set_active_warehouse(warehouse)
            void_unpaid_main(auction_id, upload_choice, show_browser, warehouse)
            return JsonResponse({'message': 'Void unpaid process started successfully'})

        except json.JSONDecodeError as e:
            logger.error(f"JSON Decode Error: {str(e)}")
            return JsonResponse({'error': f'Invalid JSON: {str(e)}'}, status=400)
        except ValueError as e:
            logger.error(f"Value Error: {str(e)}")
            return JsonResponse({'error': f'Invalid value for a parameter: {str(e)}'}, status=400)
        except Exception as e:
            logger.exception("Unexpected error in void_unpaid_view")
            return JsonResponse({'error': f'An unexpected error occurred: {str(e)}'}, status=500)

@login_required
def remove_duplicates_view(request):
    auctions = get_auction_numbers(request)
    warehouses = list(warehouse_data.keys())
    can_use_show_browser = request.user.has_perm('auction.can_use_show_browser')
    
    print("All auctions:", auctions)  # Debug print
    
    if request.method == 'POST':
        auction_number = request.POST.get('auction_number')
        target_msrp_str = request.POST.get('target_msrp')
        warehouse_name = request.POST.get('warehouse_name')
        show_browser = request.POST.get('show_browser') == 'on' and can_use_show_browser
        
        print(f"Selected: auction={auction_number}, warehouse={warehouse_name}, target_msrp={target_msrp_str}, show_browser={show_browser}")  # Debug print
        
        try:
            target_msrp = float(target_msrp_str)
        except ValueError:
            return JsonResponse({'status': 'error', 'message': 'Invalid target MSRP value'})
        
        if not auction_number or not warehouse_name:
            return JsonResponse({'status': 'error', 'message': 'Missing auction number or warehouse name'})
        
        try:
            config_manager.set_active_warehouse(warehouse_name)
            remove_duplicates_main(auction_number, target_msrp, warehouse_name, show_browser)
            result = f"Duplicates removed successfully for auction {auction_number}."
            return render(request, 'auction/result.html', {'result': result})
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            print(error_message)  # Log the error
            return JsonResponse({'status': 'error', 'message': error_message})
    
    context = {
        'auctions_json': json.dumps(auctions, cls=DjangoJSONEncoder),
        'warehouses': warehouses,
        'can_use_show_browser': can_use_show_browser,
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

        def gui_callback(message):
            logger.info(message)

        should_stop = threading.Event()

        def callback():
            logger.info("Auction formatting process completed.")

        try:
            formatter = auction_formatter_main(
                auction_id, 
                selected_warehouse, 
                gui_callback, 
                should_stop, 
                callback,
                show_browser
            )
            
            if formatter and formatter.final_csv_path and os.path.exists(formatter.final_csv_path):
                result = f"Auction {auction_id} formatted successfully. CSV file is ready for download."
                return JsonResponse({
                    'status': 'success',
                    'message': result,
                    'show_download': True,
                    'auction_id': auction_id
                })
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': f"Auction {auction_id} formatted, but CSV file was not created."
                })
        except Exception as e:
            logger.exception("Error in auction formatting process")
            return JsonResponse({
                'status': 'error',
                'message': f"An error occurred: {str(e)}"
            })

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
            logger.warning(f"Invalid auction selected: {auction_id}")
            return JsonResponse({'status': 'error', 'message': "Invalid auction selected."})

        logger.info(f"Selected auction details: {selected_auction}")
        
        ending_date_str = selected_auction.get('ending_date') or selected_auction.get('timestamp')
        
        if not ending_date_str:
            logger.error(f"No valid ending date found for auction {auction_id}")
            return JsonResponse({'status': 'error', 'message': "No valid ending date found for the selected auction."})
        
        try:
            ending_date = datetime.fromisoformat(ending_date_str.rstrip('Z')).replace(tzinfo=timezone.utc)
            logger.info(f"Parsed ending date: {ending_date}")
            ending_date_str = ending_date.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, AttributeError) as e:
            logger.error(f"Failed to parse ending date: {ending_date_str}. Error: {str(e)}")
            return JsonResponse({'status': 'error', 'message': "Invalid date format in auction data."})

        auction_title = selected_auction['title']
        
        config_manager.set_active_warehouse(selected_warehouse)

        should_stop = threading.Event()

        def gui_callback(message):
            logger.info(f"GUI Callback: {message}")

        def callback():
            logger.info("Upload completed")

        try:
            logger.info(f"Starting upload for auction {auction_id}, ending on {ending_date_str}")
            upload_to_hibid_main(
                auction_id, 
                ending_date_str,
                auction_title, 
                gui_callback, 
                should_stop, 
                callback, 
                show_browser,
                selected_warehouse
            )
            return JsonResponse({'status': 'success', 'message': f"Auction {auction_id} uploaded to HiBid successfully."})
        except Exception as e:
            logger.exception(f"Error during upload to HiBid for auction {auction_id}")
            return JsonResponse({'status': 'error', 'message': f"An error occurred: {str(e)}"})
    
    # GET request handling
    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
        'can_use_show_browser': can_use_show_browser,
    }
    return render(request, 'auction/upload_to_hibid.html', context)
