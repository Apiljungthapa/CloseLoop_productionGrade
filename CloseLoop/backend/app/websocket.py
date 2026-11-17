"""WebSocket connection manager. Clients connect to /ws?token=<JWT>.
We register each socket under its tenant_id and user_id and broadcast tenant-scoped events."""

from collections import defaultdict
from typing import Dict, Set, Any, Optional
import json
import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.tenant_conns: Dict[str, Set[WebSocket]] = defaultdict(set)
        self.user_conns: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(
        self, ws: WebSocket, tenant_id: Optional[str], user_id: Optional[str]
    ):
        await ws.accept()
        async with self._lock:
            if tenant_id:
                self.tenant_conns[tenant_id].add(ws)
            if user_id:
                self.user_conns[user_id].add(ws)

    async def disconnect(
        self, ws: WebSocket, tenant_id: Optional[str], user_id: Optional[str]
    ):
        async with self._lock:
            if tenant_id:
                self.tenant_conns[tenant_id].discard(ws)
            if user_id:
                self.user_conns[user_id].discard(ws)

    async def broadcast_tenant(
        self, tenant_id: Optional[str], event: str, payload: Any
    ):
        if not tenant_id:
            return
        msg = json.dumps({"event": event, "payload": payload}, default=str)
        dead = []
        for ws in list(self.tenant_conns.get(tenant_id, [])):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.tenant_conns[tenant_id].discard(d)

    async def send_user(self, user_id: Optional[str], event: str, payload: Any):
        if not user_id:
            return
        msg = json.dumps({"event": event, "payload": payload}, default=str)
        dead = []
        for ws in list(self.user_conns.get(user_id, [])):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.user_conns[user_id].discard(d)


manager = ConnectionManager()
