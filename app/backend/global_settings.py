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
        'HARDWARE_DEVICES_FILE': {'value': 'hardware_control/devices.json', 'type': 'str',
                                  'desc': 'Which instrument controllers the Hub polls for live sensor values, and where. Relative paths are taken from the Hub root.'},
        'LADCP_CONFIG_FILE': {'value': 'hardware_control/ladcp.json', 'type': 'str',
                              'desc': 'LADCP instruments, cruise and ADCP setup (edited on the LADCP page). Relative paths are taken from the Hub root.'},
        'HOOKS_FILE': {'value': 'hooks.json', 'type': 'str',
                       'desc': 'Lifecycle hooks: commands to run or URLs to POST when a task or workflow ends (see app/backend/hooks.py). Relative to the Hub root; absent means none.'},
        'NAV_SOURCES': {'value': '', 'type': 'str',
                        'desc': "The ship's NMEA feed, comma separated: udp:<port> listens (broadcast or unicast), tcp:<host>:<port> reads a TCP server. Empty means off. Read at start; restart the Hub after a change."},
        'NAV_LOG_DIR': {'value': '', 'type': 'str',
                        'desc': 'Where the ship track (nav_YYYYMMDD.csv, one row per fix) is written. Empty means <DATA_ROOT>/nav.'},
        'DSHIP_URL': {'value': '', 'type': 'str',
                      'desc': "The ship's DSHIP server, e.g. http://192.168.100.10:8080. The Hub reads its action log (stations, device operations) read-only. Empty means off. Restart the Hub after a change."},
        'DSHIP_USER': {'value': 'anonymous', 'type': 'str', 'desc': 'DSHIP login; anonymous may read the action log.'},
        'DSHIP_PASSWORD': {'value': 'anonymous', 'type': 'str', 'desc': 'Password for DSHIP_USER.'},
        'DSHIP_CAST_DEVICE': {'value': 'CTD', 'type': 'str',
                              'desc': 'DSHIP device short name PIScO is mounted on; its operations are numbered as casts.'},
        'DSHIP_POLL_INTERVAL': {'value': '120', 'type': 'int', 'desc': "Seconds between reads of DSHIP's action log."},
        'SHARE_MIRROR_URL': {'value': '', 'type': 'str',
                             'desc': "Folder on the ship's SMB share the instrument data and their cast overviews are copied to, e.g. //192.168.100.34/public_wiss/MSM147. Empty means no copying (the overviews are still written to <DATA_ROOT>/overview). Restart the Hub after a change."},
        'SHARE_MIRROR_USER': {'value': '', 'type': 'str', 'desc': 'Login for SHARE_MIRROR_URL.'},
        'SHARE_MIRROR_PASSWORD': {'value': '', 'type': 'str', 'desc': 'Password for SHARE_MIRROR_USER.'},
        'SHARE_MIRROR_INTERVAL': {'value': '600', 'type': 'int', 'desc': 'Seconds between overview updates and copies to the share.'},
        'SHARE_MIRROR_LADCP_DIR': {'value': '07_LADCP', 'type': 'str', 'desc': 'Subfolder of SHARE_MIRROR_URL for the LADCP data.'},
        'SHARE_MIRROR_LISST_DIR': {'value': '08_LISST', 'type': 'str', 'desc': 'Subfolder of SHARE_MIRROR_URL for the LISST data.'},
        'LISST_DIR': {'value': '', 'type': 'str', 'desc': 'Folder of the LISST-Deep raw files (L*.RBN) and their processed exports. Empty means no LISST overview.'},
        'LISST_CLOCK_OFFSET_H': {'value': '0', 'type': 'str', 'desc': "Hours to add to the LISST's clock to get UTC (MSM147: 7, the instrument runs on PDT)."},
        'MODULES_FILE': {'value': 'modules.json', 'type': 'str',
                         'desc': 'JSON list of external pages shown in the Visualization Hub. Relative paths are taken from the Hub root.'},
        'UPLOAD_ROOTS': {'value': '/mnt/nas_a,/mnt/nas_b', 'type': 'str',
                         'desc': 'Comma-separated folders the Data upload page may write into (the NAS mounts, read-write on the Hub).'},
        'UPLOAD_SSH_USER': {'value': 'upload', 'type': 'str',
                            'desc': 'Account the upload page\'s rsync command logs in as; sshd restricts it to rsync inside /mnt (rrsync).'},
        'UPLOAD_SSH_HOST': {'value': '', 'type': 'str',
                            'desc': 'Host name or address in the rsync command. Empty uses the address the page was opened with.'},
        'LINKS_FILE': {'value': 'links.json', 'type': 'str',
                       'desc': "JSON list of links on the landing page to services on the ship's network (title, url, description). Relative paths are taken from the Hub root; see links.example.json."},
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
