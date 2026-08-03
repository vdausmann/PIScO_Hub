"""Thin HTTP client for the Hub's /api/v1."""

import json
import os
from pathlib import Path

import requests

DEFAULT_URL = "http://localhost:5000"
CONFIG_PATH = Path.home() / ".config" / "pisco-hub" / "config.json"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _file_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class HubClient:
    """Resolution order for url/token: CLI flag > env var > config file."""

    def __init__(self, url=None, token=None):
        config = _file_config()
        self.base_url = (url or os.environ.get("PISCO_HUB_URL")
                         or config.get("url") or DEFAULT_URL).rstrip("/")
        self.token = (token or os.environ.get("PISCO_HUB_TOKEN")
                      or config.get("token"))

    def request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = requests.request(
                method, self.base_url + "/api/v1" + path,
                headers=headers, timeout=30, **kwargs)
        except requests.ConnectionError:
            raise ApiError(0, f"Could not connect to {self.base_url}. "
                              "Is the Hub server running?")

        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise ApiError(response.status_code,
                           data.get("error", f"HTTP {response.status_code}"))
        return data

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, payload=None):
        return self.request("POST", path, json=payload)

    def delete(self, path):
        return self.request("DELETE", path)
