from app.telegram.user.auto_renew import handlers

MODULE_NAME = "user.auto_renew"
MODULE_ENABLED = True
MODULE_ORDER = 110
_registered_clients = set()


def setup(client):
    if id(client) not in _registered_clients:
        handlers.register(client)
        _registered_clients.add(id(client))
