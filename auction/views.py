from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib import messages
from django.views.decorators.http import require_http_methods
import logging
import threading
import json
import os
from datetime import datetime
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
from auction.scripts.upload_to_hibid import upload_to_hibid_main

logger = logging.getLogger(__name__)

@login_required
def home(request):
    context = {
        'warehouses': list(warehouse_data.keys()),
        'default_warehouse': request.session.get('selected_warehouse') or list(warehouse_data.keys())[0] if warehouse_data else None,
    }
    return render(request, 'auction/home.html', context)

@login_required
def load_events():
    file_path = r"C:\Users\matt9\Desktop\auction_webapp\events.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as file:
            return json.load(file)
    return []

@login_required
def get_auction_numbers():
    try:
        with open('events.json', 'r') as f:
            events = json.load(f)
        return [
            {
                'id': event['event_id'],
                'title': event['title'],
                'timestamp': event['timestamp'],
                'warehouse': event['warehouse']
            }
            for event in events
        ]
    except (FileNotFoundError, json.JSONDecodeError):
        return []

# Load initial config
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'utils', 'config.json')

config_manager.load_config(config_path)
warehouse_data = config_manager.config.get('warehouses', {})

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
        show_browser = 'show_browser' in request.POST
        selected_warehouse = request.POST.get('selected_warehouse')

        config_manager.set_active_warehouse(selected_warehouse)
        create_auction_main(auction_title, ending_date, show_browser, selected_warehouse)
        result = f"Auction '{auction_title}' created successfully."
        return render(request, 'auction/result.html', {'result': result})
    
    # This is the only line you need to change
    warehouses = list(warehouse_data.keys())
    return render(request, 'auction/create_auction.html', {'warehouses': warehouses})

@login_required
@require_http_methods(["GET", "POST"])
def void_unpaid_view(request):
    warehouses = list(warehouse_data.keys())
    default_warehouse = warehouses[0] if warehouses else None
    events = load_events()

    if request.method == 'GET':
        context = {
            'warehouses': warehouses,
            'default_warehouse': default_warehouse,
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
            show_browser = data.get('show_browser')
            
            logger.info(f"warehouse: {warehouse}")
            logger.info(f"auction_id: {auction_id}")
            logger.info(f"upload_choice: {upload_choice}")
            logger.info(f"show_browser: {show_browser}")

            # Check for missing parameters
            required_params = ['warehouse', 'auction_id', 'upload_choice', 'show_browser']
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
    auctions = get_auction_numbers()
    warehouses = list(warehouse_data.keys())
    
    print("All auctions:", auctions)  # Debug print
    
    if request.method == 'POST':
        auction_number = request.POST.get('auction_number')
        target_msrp_str = request.POST.get('target_msrp')
        warehouse_name = request.POST.get('warehouse_name')
        
        print(f"Selected: auction={auction_number}, warehouse={warehouse_name}, target_msrp={target_msrp_str}")  # Debug print
        
        try:
            target_msrp = float(target_msrp_str)
        except ValueError:
            return JsonResponse({'status': 'error', 'message': 'Invalid target MSRP value'})
        
        if not auction_number or not warehouse_name:
            return JsonResponse({'status': 'error', 'message': 'Missing auction number or warehouse name'})
        
        try:
            config_manager.set_active_warehouse(warehouse_name)
            remove_duplicates_main(auction_number, target_msrp, warehouse_name)
            result = f"Duplicates removed successfully for auction {auction_number}."
            return render(request, 'auction/result.html', {'result': result})
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            print(error_message)  # Log the error
            return JsonResponse({'status': 'error', 'message': error_message})
    
    context = {
        'auctions_json': json.dumps(auctions, cls=DjangoJSONEncoder),
        'warehouses': warehouses,
    }
    return render(request, 'auction/remove_duplicates.html', context)

@login_required
def auction_formatter_view(request):
    warehouses = list(warehouse_data.keys())
    auctions = get_auction_numbers()  # Make sure this function is defined and returns the correct data

    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        selected_warehouse = request.POST.get('selected_warehouse')

        config_manager.set_active_warehouse(selected_warehouse)

        # Your existing code for auction formatting...
        auction_formatter_main(
            auction_id, 
            selected_warehouse, 
            print, 
            threading.Event(), 
            lambda: print("Callback")
        )

        result = f"Auction {auction_id} formatted successfully."
        return render(request, 'auction/result.html', {'result': result})

    context = {
        'warehouses': warehouses,
        'auctions': json.dumps(auctions, cls=DjangoJSONEncoder),
    }
    return render(request, 'auction/auction_formatter.html', context)

@login_required
def upload_to_hibid_view(request):
    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        ending_date = request.POST.get('ending_date')
        auction_title = request.POST.get('auction_title')
        show_browser = 'show_browser' in request.POST
        selected_warehouse = request.POST.get('selected_warehouse')
        
        config_manager.set_active_warehouse(selected_warehouse)
        upload_to_hibid_main(auction_id, ending_date, auction_title, print, lambda: False, lambda: print("Callback"), show_browser, selected_warehouse)
        
        result = f"Auction {auction_id} uploaded to HiBid successfully."
        return render(request, 'auction/result.html', {'result': result})
    
    return render(request, 'auction/upload_to_hibid.html')
