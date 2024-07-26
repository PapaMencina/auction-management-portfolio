from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import redirect
from django.contrib import messages
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

def void_unpaid_view(request):
    if request.method == 'POST':
        auction_id = request.POST.get('auction_id')
        upload_choice = int(request.POST.get('upload_choice', 0))
        show_browser = 'show_browser' in request.POST
        void_unpaid_main(auction_id, upload_choice, show_browser)
        result = "Unpaid transactions voided successfully."
        return render(request, 'auction/result.html', {'result': result})
    return render(request, 'auction/void_unpaid.html')

def remove_duplicates_view(request):
    auctions = get_auction_numbers()
    warehouses = list(warehouse_data.keys())
    
    print("All auctions:", auctions)  # Debug print
    
    if request.method == 'POST':
        auction_number = request.POST.get('auction_number')
        target_msrp = float(request.POST.get('target_msrp'))
        warehouse_name = request.POST.get('warehouse_name')
        
        print(f"Selected: auction={auction_number}, warehouse={warehouse_name}")  # Debug print
        
        remove_duplicates_main(auction_number, target_msrp, warehouse_name)
        result = f"Duplicates removed successfully for auction {auction_number}."
        return render(request, 'auction/result.html', {'result': result})
    
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
