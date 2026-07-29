"""Custom Tornado Application with native upload detach support."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type, cast

import tornado.httputil
import tornado.web

from aird.web.upload_delegate import DetachUploadDelegate


class AirdApplication(tornado.web.Application):
    def get_handler_delegate(
        self,
        request: tornado.httputil.HTTPServerRequest,
        target_class: Type[tornado.web.RequestHandler],
        target_kwargs: Optional[Dict[str, Any]] = None,
        path_args: Optional[List[bytes]] = None,
        path_kwargs: Optional[Dict[str, bytes]] = None,
    ) -> tornado.web._HandlerDelegate:
        return cast(
            tornado.web._HandlerDelegate,
            DetachUploadDelegate(
                self,
                request,
                target_class,
                target_kwargs,
                path_args,
                path_kwargs,
            ),
        )
