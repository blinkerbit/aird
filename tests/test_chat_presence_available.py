"""A locked phone stays connected without showing as online."""

from aird.plugins.chat.service import ChatHub


def _socket(available=True):
    sock = type("Sock", (), {})()
    sock._available = available
    return sock


def test_locked_phone_stays_connected_and_hides_online():
    hub = ChatHub()
    phone = _socket(available=False)
    hub.add(7, phone, "vis")
    assert hub.is_online(7)
    assert not hub.is_visible(7)
    assert 7 not in hub.online_user_ids()

    was, now = hub.set_available(phone, True)
    assert (was, now) == (False, True)
    assert 7 in hub.online_user_ids()

    was, now = hub.set_available(phone, False)
    assert (was, now) == (True, False)
    assert hub.is_online(7)
    assert 7 not in hub.online_user_ids()


def test_other_worker_hides_locked_user():
    hub = ChatHub()
    hub.handle_bus_frame({
        "op": "presence",
        "worker": -1,
        "online": [{"id": 3, "username": "usera"}],
        "visible": [],
    })
    assert hub.is_online(3)
    assert not hub.is_visible(3)
    assert 3 not in hub.online_user_ids()
