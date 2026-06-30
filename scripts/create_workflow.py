import requests
import json
import os

# Configuration
BASE_URL = "http://127.0.0.1:5000"
LOGIN_URL = f"{BASE_URL}/login"
CREATE_URL = f"{BASE_URL}/task-manager/workflow/create"

# Your login credentials
CREDENTIALS = {
    "username": "user",
    "password": "user"
}

def run_test():
    # 1. Use a session to persist the login cookie
    session = requests.Session()
    
    print("[*] Attempting to login...")
    response = session.post(LOGIN_URL, data=CREDENTIALS)
    
    if response.status_code != 200:
        print(f"[!] Login failed with status {response.status_code}")
        return

    # 2. Prepare the dummy settings files (so validation passes)
    # On NixOS, ensure these paths are writable or use /tmp
    config1 = "/tmp/test_config_1.json"
    config2 = "/tmp/test_config_2.json"
    
    for path in [config1, config2]:
        with open(path, "w") as f:
            f.write('{"param": "test"}')

    # 3. Define the Workflow Payload
    payload = {
        "name": "API Test Workflow",
        "tools": ["test_segmenter", "test_load"],
        "settings": [config1, config2]
    }

    print(f"[*] Sending workflow creation request...")
    response = session.post(
        CREATE_URL, 
        json=payload,
        headers={"Content-Type": "application/json"}
    )

    # 4. Analyze result
    if response.status_code == 201:
        print("[+] SUCCESS!")
        print(json.dumps(response.json(), indent=4))
    else:
        print(f"[!] FAILED with status {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    run_test()
