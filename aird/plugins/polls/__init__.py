from __future__ import annotations

from aird.utils.util import is_feature_enabled

PLUGIN_FLAG = "polls"


def is_polls_enabled() -> bool:
    return is_feature_enabled(PLUGIN_FLAG, False)


def register_polls(routes: list) -> None:
    from aird.plugins.polls.handlers import (
        PollDetailHandler,
        PollListHandler,
        PollPageHandler,
        PollShareHandler,
        PollVoteHandler,
    )

    pid = r"([^/]+)"
    routes.extend(
        [
            (r"/polls", PollPageHandler),
            (r"/api/polls", PollListHandler),
            (rf"/api/polls/{pid}", PollDetailHandler),
            (rf"/api/polls/{pid}/vote", PollVoteHandler),
            (rf"/api/polls/{pid}/share", PollShareHandler),
        ]
    )
