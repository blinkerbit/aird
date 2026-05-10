# Input validation

Server-side limits are defined in `aird/constants/input_limits.py` and enforced in handlers (`Content-Length`, `parse_json_body`, and field validators in `aird/core/input_validation.py`). Clients may use HTML `maxlength` for UX only; bypassing them must still fail on the server.
