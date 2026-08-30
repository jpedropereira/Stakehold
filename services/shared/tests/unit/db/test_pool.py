from shared.db.pool import connection_pool


def test_connection_pool_is_created_closed():
    pool = connection_pool("postgresql://user:pass@localhost/db", min_size=1, max_size=5)

    assert pool.closed is True


def test_connection_pool_passes_size_bounds():
    pool = connection_pool("postgresql://user:pass@localhost/db", min_size=2, max_size=7)

    assert pool.min_size == 2
    assert pool.max_size == 7
