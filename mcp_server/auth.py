"""Bearer-token auth middleware for the MCP streamable HTTP server.

Fails closed: if MCP_API_KEY isn't set, every request is rejected rather
than silently allowing anonymous access once the server is tunneled to
the public internet.
"""

import os


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app
        self.api_key = os.environ.get("MCP_API_KEY")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not self.api_key or auth_header != f"Bearer {self.api_key}":
            body = b'{"error": "unauthorized"}'
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
