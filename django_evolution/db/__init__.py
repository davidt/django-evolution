from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from django_evolution.db.common import BaseEvolutionOperations
    from django_evolution.db.state import DatabaseState


class EvolutionOperationsMulti:

    ######################
    # Instance variables #
    ######################

    #: The evolution operations object for the database.
    evolver: BaseEvolutionOperations

    def __init__(
        self,
        db_name: str,
        database_state: (DatabaseState | None) = None,
    ) -> None:
        """Initialize the instance.

        Args:
            db_name (str):
                The name of the database.

            database_state (django_evolution.db.state.DatabaseState):
                The database state to track information through.
        """
        if database_state is None:
            from django_evolution.db.state import DatabaseState
            database_state = DatabaseState(db_name, scan=False)

        from django.db import connections
        engine = settings.DATABASES[db_name]['ENGINE'].split('.')[-1]
        connection = connections[db_name]
        module_name = ['django_evolution.db', engine]
        module = __import__('.'.join(module_name), {}, {}, [''])
        self.evolver = module.EvolutionOperations(database_state,
                                                  connection)

    def get_evolver(self) -> BaseEvolutionOperations:
        """Return the evolver instance.

        Returns:
            django_evolution.db.common.BaseEvolutionOperations:
            The evolver instance.
        """
        return self.evolver
