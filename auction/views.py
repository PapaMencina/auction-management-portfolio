from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import redirect
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import logging
import json
import os
from datetime import datetime
from auction.utils import config_manager
from auction.scripts.create_auction import create_auction_main
from auction.scripts.void_unpaid_on_bid import void_unpaid_main
from auction.scripts.remove_duplicates_in_airtable import remove_duplicates_main
from auction.scripts.auction_formatter import auction_formatter_main
from auction.scripts.upload_to_hibid import upload_to_hibid_main

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

with open(config_path, 'r') as f:
    config = json.load(f)
warehouse_data = config.get('warehouses', {})

def home(request):
    context = {
        'warehouses': list(warehouse_data.keys()),
        'default_warehouse': request.session.get('selected_warehouse') or list(warehouse_data.keys())[0] if warehouse_data else None,
    }
    return render(request, 'auction/home.html', context)

from django.shortcuts import redirect

def select_warehouse(request):
    if request.method == 'POST':
        selected_warehouse = request.POST.get('warehouse')
        if selected_warehouse in warehouse_data:
            config_manager.load_config(config_path, selected_warehouse)
            request.session['selected_warehouse'] = selected_warehouse
            # Add a message to show on the home page
            messages.success(request, f"Warehouse {selected_warehouse} selected and configuration loaded.")
        else:
            messages.error(request, "Invalid warehouse selection.")
    return redirect('home')  # Always redirect to home

def create_auction_view(request):
    if request.method == 'POST':
        auction_title = request.POST.get('auction_title')
        ending_date = datetime.strptime(request.POST.get('ending_date'), '%Y-%m-%d')
        show_browser = 'show_browser' in request.POST
        selected_warehouse = request.POST.get('selected_warehouse')

        create_auction_main(auction_title, ending_date, show_browser, selected_warehouse)
        result = f"Auction '{auction_title}' created successfully."
        return render(request, 'auction/result.html', {'result': result})
    return render(request, 'auction/create_auction.html', {'warehouses': list(warehouse_data.keys())})

logger = logging.getLogger(__name__)

@require_http_methods(["GET", "POST"])
def void_unpaid_view(request):
    if request.method == 'GET':
        # Render the form for GET requests
        return render(request, 'auction/void_unpaid.html')

    elif request.method == 'POST':
        logger.info("Received POST request to void_unpaid_view")
        logger.info(f"Request headers: {request.headers}")

        try:
            body = request.body.decode('utf-8')
            logger.info(f"Request body: {body}")
            
            data = json.loads(body)
            logger.info(f"Parsed JSON data: {data}")
            
            auction_id = data.get('auction_id')
            upload_choice = data.get('upload_choice')
            show_browser = data.get('show_browser')
            
            logger.info(f"auction_id: {auction_id}")
            logger.info(f"upload_choice: {upload_choice}")
            logger.info(f"show_browser: {show_browser}")

            # Check for missing parameters
            required_params = ['auction_id', 'upload_choice', 'show_browser', ]
            missing = [param for param in required_params if data.get(param) is None]
            if missing:
                return JsonResponse({'error': f'Missing required parameters: {", ".join(missing)}'}, status=400)

            # Convert types
            upload_choice = int(upload_choice)
            show_browser = bool(show_browser)

            # Call the main function
            void_unpaid_main(auction_id, upload_choice, show_browser,)
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

def auction_formatter_view(request):
    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        selected_warehouse = request.POST.get('selected_warehouse')
        auction_formatter_main(auction_id, selected_warehouse, print, lambda: False, lambda: print("Callback"))
        result = f"Auction {auction_id} formatted successfully."
        return render(request, 'auction/result.html', {'result': result})
    return render(request, 'auction/auction_formatter.html')

def upload_to_hibid_view(request):
    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        ending_date = request.POST.get('ending_date')
        auction_title = request.POST.get('auction_title')
        show_browser = 'show_browser' in request.POST
        selected_warehouse = request.POST.get('selected_warehouse')
        upload_to_hibid_main(auction_id, ending_date, auction_title, print, lambda: False, lambda: print("Callback"), show_browser, selected_warehouse)
        result = f"Auction {auction_id} uploaded to HiBid successfully."
        return render(request, 'auction/result.html', {'result': result})
    return render(request, 'auction/upload_to_hibid.html')
