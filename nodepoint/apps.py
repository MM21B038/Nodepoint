import sys

from django.apps import AppConfig


class NodepointConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nodepoint"

    def ready(self) -> None:
        if not {"rqworker", "rqworker-pool"}.intersection(sys.argv):
            return

        import django_rq.utils

        from nodepoint.mongo.manager import reset_mongo_connection

        reset_db_connections = django_rq.utils.reset_db_connections

        def reset_connections() -> None:
            reset_db_connections()
            reset_mongo_connection()

        django_rq.utils.reset_db_connections = reset_connections

        if "orchestrator" in sys.argv:
            from nodepoint.services.preprocess_recovery import maybe_run_startup_recovery

            maybe_run_startup_recovery()
