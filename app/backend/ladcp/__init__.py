"""LADCP control and live view: two RDI WorkHorse ADCPs on the CTD
rosette, configured, started, watched and downloaded from the Hub. It
replaces the AutoIt/TeraTerm ladcp_tool and grows out of Marco Schulz's
ladcp_live (git.geomar.de). See docs/ladcp.md."""

import os

from ..models import GlobalSetting


def create_service(app):
    from .service import LadcpService
    root = app.config["HUB_ROOT"]
    with app.app_context():
        path = GlobalSetting.get("LADCP_CONFIG_FILE", "hardware_control/ladcp.json")
        data_root = GlobalSetting.get("DATA_ROOT", os.path.join(root, "data"))
    if not os.path.isabs(path):
        path = os.path.join(root, path)
    service = LadcpService(path, os.path.join(root, "hardware_control", "state", "ladcp.json"),
                           data_root)
    app.extensions["ladcp"] = service
    return service
