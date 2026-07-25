"""RQ worker hooks (install from rqworker command handle, not AppConfig.ready)."""


def install_rq_worker_hooks() -> None:
    import django_rq.utils

    from nodepoint.mongo.manager import reset_mongo_connection

    reset_db_connections = django_rq.utils.reset_db_connections

    def reset_connections() -> None:
        reset_db_connections()
        reset_mongo_connection()

    django_rq.utils.reset_db_connections = reset_connections
