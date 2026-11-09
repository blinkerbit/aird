from __future__ import annotations

from aird.utils.util import is_feature_enabled

PLUGIN_FLAG = "lists"


def is_lists_enabled() -> bool:
    return is_feature_enabled(PLUGIN_FLAG, False)


def register_lists(routes: list) -> None:
    from aird.plugins.lists.handlers import (
        ListDetailHandler,
        ListGroupHandler,
        ListGroupListHandler,
        ListItemHandler,
        ListItemsHandler,
        ListListHandler,
        ListOrderHandler,
        ListPageHandler,
        ListPlaceHandler,
        ListShareHandler,
    )

    lid = r"([^/]+)"
    iid = r"([^/]+)"
    routes.extend(
        [
            (r"/lists", ListPageHandler),
            (r"/api/list-groups", ListGroupListHandler),
            (rf"/api/list-groups/{lid}", ListGroupHandler),
            (r"/api/lists", ListListHandler),
            (rf"/api/lists/{lid}/group", ListPlaceHandler),
            (rf"/api/lists/{lid}/order", ListOrderHandler),
            (rf"/api/lists/{lid}/items/{iid}", ListItemHandler),
            (rf"/api/lists/{lid}/items", ListItemsHandler),
            (rf"/api/lists/{lid}", ListDetailHandler),
            (rf"/api/lists/{lid}/share", ListShareHandler),
        ]
    )
