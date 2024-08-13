import os
from django.conf import settings

def get_resource_path(resource_type, filename=None):
    base_path = os.path.join(settings.BASE_DIR, 'auction', 'resources')
    
    resource_paths = {
        'processed_csv': os.path.join(base_path, 'processed_csv'),
        'hibid_csv': os.path.join(base_path, 'hibid_csv'),
        'hibid_images': os.path.join(base_path, 'hibid_images'),
        'bid_stock_photo': os.path.join(base_path, 'bid_stock_photo'),
        'downloads': os.path.join(base_path, 'downloads'),
    }
    
    if resource_type not in resource_paths:
        raise ValueError(f"Unknown resource type: {resource_type}")
    
    path = resource_paths[resource_type]
    
    if filename:
        path = os.path.join(path, filename)
    
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    return path