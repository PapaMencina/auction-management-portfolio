import json
import os
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import logging

logger = logging.getLogger(__name__)

config = {}
active_warehouse = None

def load_config(config_path=None, warehouse_name=None):
    global config, active_warehouse
    try:
        if config_path is None:
            config_path = os.path.join(settings.BASE_DIR, 'auction', 'utils', 'config.json')
        
        logger.debug(f"Attempting to load config from: {config_path}")
        
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Config file not found at {config_path}")
        
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Update file_path_702_logo for each warehouse
        for warehouse in config['warehouses'].values():
            warehouse['file_path_702_logo'] = os.path.join(os.path.dirname(config_path), '..', 'resources', 'bid_stock_photo', '702_logo.png')
        
        logger.info("Configuration loaded successfully")
        if warehouse_name and warehouse_name in config['warehouses']:
            set_active_warehouse(warehouse_name)
        elif config['warehouses']:
            default_warehouse = next(iter(config['warehouses']))
            set_active_warehouse(default_warehouse)
            logger.info(f"No warehouse specified. Set to default: {default_warehouse}")
        else:
            logger.warning(f"No warehouses found in config.")
    except Exception as e:
        logger.error(f"Error loading configuration: {str(e)}")
        logger.exception("Full traceback:")
        config = {}

def set_active_warehouse(warehouse_name):
    global active_warehouse
    if warehouse_name in config.get('warehouses', {}):
        active_warehouse = warehouse_name
        logger.info(f"Active warehouse set to: {warehouse_name}")
        logger.debug(f"Warehouse config: {config['warehouses'][active_warehouse]}")
    else:
        logger.warning(f"Warehouse '{warehouse_name}' not found in config. Available warehouses: {', '.join(config['warehouses'].keys())}")
        active_warehouse = None

def get_global_var(var_name):
    value = config.get('global', {}).get(var_name)
    if value is None:
        raise ImproperlyConfigured(f"Global variable '{var_name}' is not set in the config.")
    return value

def get_warehouse_var(var_name):
    if active_warehouse:
        value = config['warehouses'].get(active_warehouse, {}).get(var_name)
        if value is None:
            raise ImproperlyConfigured(f"Warehouse variable '{var_name}' is not set for '{active_warehouse}'.")
        return value
    else:
        logger.error(f"Active warehouse is not set. Cannot retrieve {var_name}.")
        raise ImproperlyConfigured("Active warehouse is not set.")

def get_all_warehouses():
    return list(config.get('warehouses', {}).keys())

# Load the configuration when the module is imported
load_config()