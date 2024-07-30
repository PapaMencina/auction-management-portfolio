import json

config = {}

def load_config(config_path):
    global config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print("Configuration loaded successfully")
    except Exception as e:
        print(f"Error loading configuration: {e}")
        config = {}

def get_global_var(var_name):
    for warehouse in config.get('warehouses', {}).values():
        if var_name in warehouse:
            value = warehouse[var_name]
            print(f"Retrieved {var_name}: {value}")
            return value
    print(f"Variable {var_name} not found in config")
    return None