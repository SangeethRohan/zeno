from . import database, ubuntu, web


def register_create_routes(app, client, deps):
    """Register all create-menu API routes on the Flask app."""
    database.register_routes(app, client, deps)
    ubuntu.register_routes(app, client, deps)
    web.register_routes(app, client, deps)
