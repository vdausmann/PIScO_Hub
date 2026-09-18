# Documentation for Hardware Control
- Configuration is done via the "hardware_control_config.json" file
- Format of the config file:
```json
    {
        "Device 1": {
            "sensors": [
                {
                    "shorthand": "Pi-ExPres",
                    "name": "External Pressure",
                    "group": "(Gruppe PIScO)",
                    "type": "float",
                    "format": {
                      "formatter": "xxxx.xx",
                      "unit": "dbar"
                    },
                    "min": null,
                    "max": "6000",
                    "alarm condition": null
                }
            ],
        "commands": [
            {
                "shorthand": "Pi-StartRec",
                "name": "start Recording  ",
                "group": "(Gruppe PIScO)",
				"type": "bool",
				"format": {
				  "formatter": "button",
				  "colors": ["red", "green", "gray"],
				  "unit": ""
				},
                "min": null,
                "max": null,
                "alarm condition": "Pi-ExPres>=500"
            }
        },
        "Device 2": {
            ...
        }
    }
```
- supported operators for the alarm condition are: 
    ==, !=, >=, <=, >, <
