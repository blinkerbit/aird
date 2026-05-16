# Input validation

Server-side limits are defined in `aird/constants/input_limits.py` and strictly enforced by the backend via dedicated validation modules. 

Key validation patterns:
1. **Data Contracts (`aird/domain/contracts.py`)**: All incoming JSON payloads for critical endpoints (like share creation) should be unmarshalled and validated using standard Python dataclasses before processing. This ensures strict type adherence and field presence.
2. **Length Enforcement (`InputTooLongError`)**: Handlers use `Content-Length`, `parse_json_body`, and specific field validators in `aird/core/input_validation.py`. Oversized inputs immediately trigger an `InputTooLongError` which short-circuits the operation and returns a 400-level HTTP status.
3. **Defense in Depth**: Client-side UI limits (e.g., HTML `maxlength`) are strictly for UX purposes. The Python `input_validation` layer is the authoritative security boundary.
