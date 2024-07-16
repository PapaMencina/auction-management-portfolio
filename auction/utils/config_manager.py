# config_manager.py
import json

config = {}
current_warehouse = 'Maule Warehouse'  # Default warehouse

def load_config(config_path, warehouse_name):
    global config, current_warehouse
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        if 'warehouses' not in config:
            raise KeyError("Key 'warehouses' not found in the configuration file.")
        if warehouse_name not in config['warehouses']:
            raise KeyError(f"Warehouse '{warehouse_name}' not found in the configuration file.")
        current_warehouse = warehouse_name
        print(f"Configuration loaded for {warehouse_name}")
    except Exception as e:
        print(f"Error loading configuration: {e}")
        config = {}

def set_current_warehouse(warehouse_name):
    global current_warehouse
    if warehouse_name in config.get('warehouses', {}):
        current_warehouse = warehouse_name
    else:
        raise KeyError(f"Warehouse '{warehouse_name}' not found in the configuration.")

def get_global_var(var_name):
    if current_warehouse in config.get('warehouses', {}):
        value = config['warehouses'][current_warehouse].get(var_name, None)
        print(f"Retrieved {var_name} for {current_warehouse}: {value}")
        return value
    print(f"Warehouse {current_warehouse} not found in config")
    return None
