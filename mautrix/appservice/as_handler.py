# Copyright (c) 2020 Tulir Asokan
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# Partly based on github.com/Cadair/python-appservice-framework (MIT license)
from typing import Optional, Callable, Awaitable, List, Set
from json import JSONDecodeError
from aiohttp import web
import asyncio
import logging

from ..api import JSON
from ..types import UserID, RoomAlias, Event, SerializerError

QueryFunc = Callable[[web.Request], Awaitable[Optional[web.Response]]]
HandlerFunc = Callable[[Event], Awaitable]


class AppServiceServerMixin:
    loop: asyncio.AbstractEventLoop
    log: logging.Logger

    hs_token: str

    query_user: Callable[[UserID], JSON]
    query_alias: Callable[[RoomAlias], JSON]

    transactions: Set[str]
    event_handlers: List[HandlerFunc]

    def __init__(self) -> None:
        self.transactions = set()
        self.event_handlers = []

        async def default_query_handler(_):
            return None

        self.query_user = default_query_handler
        self.query_alias = default_query_handler

    def register_routes(self, app: web.Application) -> None:
        app.router.add_route("PUT", "/transactions/{transaction_id}",
                             self._http_handle_transaction)
        app.router.add_route("GET", "/rooms/{alias}", self._http_query_alias)
        app.router.add_route("GET", "/users/{user_id}", self._http_query_user)
        app.router.add_route("PUT", "/_matrix/app/v1/transactions/{transaction_id}",
                             self._http_handle_transaction)
        app.router.add_route("GET", "/_matrix/app/v1/rooms/{alias}", self._http_query_alias)
        app.router.add_route("GET", "/_matrix/app/v1/users/{user_id}", self._http_query_user)

    def _check_token(self, request: web.Request) -> bool:
        try:
            token = request.rel_url.query["access_token"]
        except KeyError:
            try:
                token = request.headers["Authorization"].lstrip("Bearer ")
            except (KeyError, AttributeError):
                return False

        if token != self.hs_token:
            return False

        return True

    async def _http_query_user(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(status=401)

        try:
            user_id = request.match_info["user_id"]
        except KeyError:
            return web.Response(status=400)

        try:
            response = await self.query_user(user_id)
        except Exception:
            self.log.exception("Exception in user query handler")
            return web.Response(status=500)

        if not response:
            return web.Response(status=404)
        return web.json_response(response)

    async def _http_query_alias(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(status=401)

        try:
            alias = request.match_info["alias"]
        except KeyError:
            return web.Response(status=400)

        try:
            response = await self.query_alias(alias)
        except Exception:
            self.log.exception("Exception in alias query handler")
            return web.Response(status=500)

        if not response:
            return web.Response(status=404)
        return web.json_response(response)

    async def _http_handle_transaction(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(status=401)

        transaction_id = request.match_info["transaction_id"]
        if transaction_id in self.transactions:
            return web.json_response({})

        try:
            json = await request.json()
        except JSONDecodeError:
            return web.Response(status=400)

        try:
            events = json["events"]
        except KeyError:
            return web.Response(status=400)

        try:
            await self.handle_transaction(transaction_id, events)
        except Exception:
            self.log.exception("Exception in transaction handler")

        self.transactions.add(transaction_id)

        return web.json_response({})

    async def handle_transaction(self, txn_id: str, events: List[JSON]) -> None:
        for raw_event in events:
            try:
                event = Event.deserialize(raw_event)
            except SerializerError:
                self.log.exception("Failed to deserialize event %s", raw_event)
            else:
                self.handle_matrix_event(event)

    def handle_matrix_event(self, event: Event) -> None:
        if event.type.is_state and event.state_key is None:
            self.log.debug(f"Not sending {event.event_id} to handlers: expected state_key.")
            return

        async def try_handle(handler_func: HandlerFunc):
            try:
                await handler_func(event)
            except Exception:
                self.log.exception("Exception in Matrix event handler")

        for handler in self.event_handlers:
            self.loop.create_task(try_handle(handler))

    def matrix_event_handler(self, func: HandlerFunc) -> HandlerFunc:
        self.event_handlers.append(func)
        return func
