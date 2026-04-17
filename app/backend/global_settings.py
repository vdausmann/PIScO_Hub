from .models import db, GlobalSetting

def seed_settings(app):
    defaults = {
        'MAX_WEIGHT': {'value': '10', 'type': 'int', 'desc': 'Maximum concurrent resource weight.'},
        'POLL_INTERVAL': {'value': '5', 'type': 'int', 'desc': 'Seconds between worker cycles.'},
        'LOG_RETENTION_DAYS': {'value': '30', 'type': 'int', 'desc': 'How long to keep log files.'},
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
