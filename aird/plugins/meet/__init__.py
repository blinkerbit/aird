from __future__ import annotations

from aird.utils.util import is_feature_enabled

PLUGIN_FLAG = "meet"


def is_meet_enabled() -> bool:
    return is_feature_enabled(PLUGIN_FLAG, False)


def register_meet(routes: list) -> None:
    from aird.plugins.meet.handlers import (
        MeetDetailHandler,
        MeetJoinPageHandler,
        MeetListHandler,
        MeetPageHandler,
        MeetShareHandler,
        MeetTokenHandler,
    )

    mid = r"([^/]+)"
    slug = r"([^/]+)"
    routes.extend(
        [
            (r"/meet", MeetPageHandler),
            (rf"/meet/{slug}", MeetJoinPageHandler),
            (r"/api/meet", MeetListHandler),
            (rf"/api/meet/{mid}", MeetDetailHandler),
            (rf"/api/meet/{mid}/token", MeetTokenHandler),
            (rf"/api/meet/{mid}/share", MeetShareHandler),
        ]
    )
