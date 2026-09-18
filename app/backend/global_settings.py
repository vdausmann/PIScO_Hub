import os

from .models import db, GlobalSetting

def seed_settings(app):
    defaults = {
        'MAX_WEIGHT': {'value': '10', 'type': 'int', 'desc': 'Capacity used to seed the local machine. Per-machine limits are set on the machines themselves.'},
        'MACHINE_PROBE_INTERVAL': {'value': '30', 'type': 'int', 'desc': 'Seconds between machine reachability checks.'},
        'TASK_TIMEOUT': {'value': '0', 'type': 'int', 'desc': 'Seconds a task may run before it is stopped, for tools that declare no timeout of their own. 0 means no limit.'},
        'POLL_INTERVAL': {'value': '5', 'type': 'int', 'desc': 'Seconds between worker cycles.'},
        'LOG_RETENTION_DAYS': {'value': '30', 'type': 'int', 'desc': 'How long to keep log files.'},
        'FILE_EXPLORER_ROOT': {'value': '/home/tim/', 'type': 'str', 'desc': 'Root of file explorer.'},
        'DATA_ROOT': {'value': os.path.join(app.config["HUB_ROOT"], "data"), 'type': 'str',
                      'desc': 'Root for Hub-allocated task outputs. Must be on shared storage when tasks run on other machines.'},
        'HARDWARE_CONTROL_CONFIG_FILE': {'value':
                                         "hardware_control/hardware_control_config.json",
                                         'type': 'str', 'desc': "Config file for hardware control."},
        'MODULES_FILE': {'value': 'modules.json', 'type': 'str',
                         'desc': 'JSON list of external pages shown in the Visualization Hub. Relative paths are taken from the Hub root.'},
    }

    with app.app_context():
        for key, data in defaults.items():
            if GlobalSetting.query.get(key) is None:
                new_setting = GlobalSetting()
                new_setting.key = key 
                new_setting.value = data['value']
                new_setting.setting_type = data['type']
                new_setting.description = data['desc']
                db.session.add(new_setting)
                print(new_setting.key, new_setting.value, new_setting.setting_type, new_setting.description)
        db.session.commit()
