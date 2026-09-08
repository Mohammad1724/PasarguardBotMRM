"""Package entry point for admin gift code management."""

from app.telegram.admin.gift_codes import callbacks, messages

MODULE_NAME = "admin.gift_codes"
MODULE_ENABLED = True
MODULE_ORDER = 1000
MODULE_DESCRIPTION = "Admin gift code management (balance/days/volume)"

_registered_clients: set[int] = set()


def setup(client):
    client_id = id(client)
    if client_id in _registered_clients:
        return
    messages.register(client)
    callbacks.register(client)
    _registered_clients.add(client_id)
