import json

config = {}
active_warehouse = None

def load_config(config_path, warehouse_name=None):
    global config, active_warehouse
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("Configuration loaded successfully")
        if warehouse_name:
            set_active_warehouse(warehouse_name)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        config = {}

def set_active_warehouse(warehouse_name):
    global active_warehouse
    if warehouse_name in config.get('warehouses', {}):
        active_warehouse = warehouse_name
        print(f"Active warehouse set to: {warehouse_name}")
        # Debug: Print the warehouse-specific config
        print(f"Warehouse config: {config['warehouses'][active_warehouse]}")
    else:
        print(f"Warehouse {warehouse_name} not found in config")
        active_warehouse = None

def get_global_var(var_name):
    return config.get('global', {}).get(var_name)

def get_warehouse_var(var_name):
    if active_warehouse:
        return config['warehouses'].get(active_warehouse, {}).get(var_name)
    else:
        print(f"Active warehouse is not set. Cannot retrieve {var_name}.")
        return None
