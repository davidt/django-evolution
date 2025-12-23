"""Constants used throughout Django Evolution."""

from __future__ import annotations


# TODO: Replace with StrEnum once we're on Python 3.11+
class UpgradeMethod:
    """Upgrade methods available for an application."""

    #: The app is upgraded through Django Evolution.
    EVOLUTIONS = 'evolutions'

    #: The app is upgraded through Django Migrations.
    MIGRATIONS = 'migrations'


# TODO: Replace with StrEnum once we're on Python 3.11+
class EvolutionsSource:
    """The source for an app's evolutions."""

    #: The evolutions are provided by the app.
    APP = 'app'

    #: The evolutions are built-in to Django Evolution.
    BUILTIN = 'builtin'

    #: The evolutions are provided custom by the project.
    PROJECT = 'project'
