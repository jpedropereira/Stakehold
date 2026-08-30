from psycopg_pool import ConnectionPool


def connection_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """Build a pool, closed. The caller opens it in its own lifespan and closes it on shutdown.

    Created closed (`open=False`) rather than eagerly connecting here: an HTTP service's lifespan
    needs to control exactly when the first connection attempt happens (and be able to await it),
    which an implicit open in a module-level import can't give it.
    """
    return ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=False)
