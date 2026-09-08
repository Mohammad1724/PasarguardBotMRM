"""V1 customer experience: namespaced callbacks, registered before legacy user flows."""

from app.telegram.user.customer_experience import handlers

MODULE_NAME = "user.customer_experience"
MODULE_ENABLED = True
MODULE_ORDER = 100
_registered_clients = set()


def setup(client):
    if id(client) not in _registered_clients:
        handlers.register(client)
        _registered_clients.add(id(client))
