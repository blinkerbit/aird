import pytest
pytest.skip("requires running server and manual setup", allow_module_level=True)
import asyncio
import websockets
import sys

# This test demonstrates the disabled WebSocket origin check.
# It connects to the WebSocket endpoint from a different origin.

# --- Configuration ---
# The WebSocket URL of the application
WEBSOCKET_URL = "ws://localhost:8000/stream/test.txt"
# The origin to use for the WebSocket connection
ORIGIN = "http://evil.com"

# --- Test ---
async def test_websocket_origin():
    """
    Attempts to connect to the WebSocket endpoint with a different origin.
    """
    try:
        async with websockets.connect(WEBSOCKET_URL, origin=ORIGIN) as websocket:
            print("WebSocket connection successful with a different origin.")
            # If the connection is successful, the vulnerability is confirmed.
            # We can then close the connection.
            await websocket.close()
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"WebSocket connection failed with status code {e.statusCode}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Create a dummy file for the streaming endpoint to open
    with open("test.txt", "w") as f:
        f.write("This is a test file.")

    asyncio.run(test_websocket_origin())

    # Clean up the dummy file
    import os
    os.remove("test.txt")
