"""Utilities for working with evolutions and mutations."""

from __future__ import unicode_literals

import os
from importlib import import_module

from django.conf import settings
from django.db import connections
from django.db.utils import DEFAULT_DB_ALIAS

from django_evolution.builtin_evolutions import BUILTIN_SEQUENCES
from django_evolution.consts import EvolutionsSource, UpgradeMethod
from django_evolution.errors import EvolutionException
from django_evolution.support import supports_migrations
from django_evolution.utils.apps import get_app_label, get_app_name
from django_evolution.utils.migrations import (get_applied_migrations_by_app,
                                               has_migrations_module)


def has_evolutions_module(app):
    """Return whether an app has an evolutions module.

    Args:
        app (module):
            The app module.

    Returns:
        bool:
        ``True`` if the app has an ``evolutions`` module. ``False`` if it
        does not.
    """
    return get_evolutions_module(app) is not None


def get_evolutions_source(app):
    """Return the source for evolutions.

    This is used to determine where evolutions are coming from. They can be
    provided by the app, project, or built into Django Evolution.

    Args:
        app (module):
            The app module.

    Returns:
        unicode:
        The evolution source. This is an entry from
        :py:class:`~django_evolution.consts.EvolutionsSource`.
    """
    app_name = get_app_name(app)

    if app_name in BUILTIN_SEQUENCES:
        return EvolutionsSource.BUILTIN
    elif app_name in getattr(settings, 'CUSTOM_EVOLUTIONS', {}):
        return EvolutionsSource.PROJECT
    else:
        return EvolutionsSource.APP


def get_evolutions_module(app):
    """Return the evolutions module for an app.

    Args:
        app (module):
            The app.

    Returns:
        module:
        The evolutions module for the app, or ``None`` if it could not be
        found.
    """
    app_name = get_app_name(app)

    if app_name in BUILTIN_SEQUENCES:
        module_name = 'django_evolution.builtin_evolutions'
    elif app_name in getattr(settings, 'CUSTOM_EVOLUTIONS', {}):
        module_name = settings.CUSTOM_EVOLUTIONS[app_name]
    else:
        module_name = '%s.evolutions' % app_name

    try:
        return import_module(module_name)
    except ImportError:
        return None


def get_evolutions_path(app):
    """Return the evolutions path for an app.

    Args:
        app (module):
            The app.

    Returns:
        str:
        The path to the evolutions module for the app, or ``None`` if it
        could not be found.
    """
    module = get_evolutions_module(app)

    if module:
        return os.path.dirname(module.__file__)

    return None


def get_evolution_sequence(app):
    """Return the list of evolution labels for a Django app.

    Args:
        app (module):
            The app to return evolutions for.

    Returns:
        list of unicode:
        The list of evolution labels.
    """
    app_name = get_app_name(app)

    if app_name in BUILTIN_SEQUENCES:
        return BUILTIN_SEQUENCES[app_name]

    module = get_evolutions_module(app)

    if module is not None:
        return module.SEQUENCE

    return []


def get_unapplied_evolutions(app, database=DEFAULT_DB_ALIAS):
    """Return the list of labels for unapplied evolutions for a Django app.

    Args:
        app (module):
            The app to return evolutions for.

        database (unicode, optional):
            The name of the database containing the
            :py:class:`~django_evolution.models.Evolution` entries.

    Returns:
        list of unicode:
        The labels of evolutions that have not yet been applied.
    """
    # Avoids a nasty circular import. Util modules should always be
    # importable, so we compensate here.
    from django_evolution.models import Evolution

    applied = set(
        Evolution.objects
        .using(database)
        .filter(app_label=get_app_label(app))
        .values_list('label', flat=True)
    )

    return [
        evolution_name
        for evolution_name in get_evolution_sequence(app)
        if evolution_name not in applied
    ]


def get_applied_evolutions(app, database=DEFAULT_DB_ALIAS):
    """Return the list of labels for applied evolutions for a Django app.

    Args:
        app (module):
            The app to return evolutions for.

        database (unicode, optional):
            The name of the database containing the
            :py:class:`~django_evolution.models.Evolution` entries.

    Returns:
        list of unicode:
        The labels of evolutions that have been applied.
    """
    # Avoids a nasty circular import. Util modules should always be
    # importable, so we compensate here.
    from django_evolution.models import Evolution

    return list(
        Evolution.objects
        .using(database)
        .filter(app_label=get_app_label(app))
        .values_list('label', flat=True)
    )


def get_app_mutations(app, evolution_labels=None, database=DEFAULT_DB_ALIAS):
    """Return the mutations on an app provided by the given evolution names.

    Args:
        app (module):
            The app the evolutions belong to.

        evolution_labels (list of unicode, optional):
            The labels of the evolutions to return mutations for.

            If ``None``, this will factor in all evolution labels for the
            app.

        database (unicode, optional):
            The name of the database the evolutions cover.

    Returns:
        list of django_evolution.mutations.BaseMutation:
        The list of mutations provided by the evolutions.

    Raises:
        django_evolution.errors.EvolutionException:
            One or more evolutions are missing.
    """
    # Avoids a nasty circular import. Util modules should always be
    # importable, so we compensate here.
    from django_evolution.mutations import SQLMutation

    evolutions_module = get_evolutions_module(app)

    if evolutions_module is None:
        return []

    mutations = []
    directory_name = os.path.dirname(evolutions_module.__file__)

    if evolution_labels is None:
        evolution_labels = get_evolution_sequence(app)

    for label in evolution_labels:
        # The first element is used for compatibility purposes.
        filenames = [
            os.path.join(directory_name, '%s.sql' % label),
            os.path.join(directory_name, '%s_%s.sql' % (database, label)),
        ]

        found = False

        for filename in filenames:
            if os.path.exists(filename):
                with open(filename, 'r') as fp:
                    sql = fp.readlines()

                mutations.append(SQLMutation(label, sql))

                found = True
                break

        if not found:
            try:
                module = import_module('%s.%s' % (evolutions_module.__name__,
                                                  label))
                mutations += module.MUTATIONS
            except ImportError:
                raise EvolutionException(
                    'Error: Failed to find an SQL or Python evolution named %s'
                    % label)

    return mutations


def get_app_pending_mutations(app, evolution_labels,
                              database=DEFAULT_DB_ALIAS):
    """Return an app's pending mutations provided by the given evolution names.

    This is similar to :py:meth:`get_app_mutations`, but filters the list
    of mutations down to remove any that are unnecessary (ones that do not
    operate on changed parts of the project signature).

    Args:
        app (module):
            The app the evolutions belong to.

        evolution_labels (list of unicode, optional):
            The labels of the evolutions to return mutations for.

            If ``None``, this will factor in all evolution labels for the
            app.

        database (unicode, optional):
            The name of the database the evolutions cover.

    Returns:
        list of django_evolution.mutations.BaseMutation:
        The list of mutations provided by the evolutions.

    Raises:
        django_evolution.errors.EvolutionException:
            One or more evolutions are missing.
    """
    # Avoids a nasty circular import. Util modules should always be
    # importable, so we compensate here.
    from django_evolution.models import Version
    from django_evolution.mutations import RenameModel
    from django_evolution.signature import ProjectSignature

    mutations = get_app_mutations(app=app,
                                  evolution_labels=evolution_labels,
                                  database=database)

    latest_version = Version.objects.current_version(using=database)

    app_id = get_app_label(app)
    old_project_sig = latest_version.signature
    project_sig = ProjectSignature.from_database(database)

    old_app_sig = old_project_sig.get_app_sig(app_id)
    app_sig = project_sig.get_app_sig(app_id)

    if old_app_sig is not None and app_sig is not None:
        # We want to go through now and make sure we're only applying
        # evolutions for models where the signature is different between
        # what's stored and what's current.
        #
        # The reason for this is that we may have just installed a baseline,
        # which would have the up-to-date signature, and we might be trying
        # to apply evolutions on top of that (which would already be applied).
        # These would generate errors. So, try hard to prevent that.
        #
        # First, Find the list of models in the latest signature of this app
        # that aren't in the old signature.
        changed_models = set(
            model_sig.model_name
            for model_sig in app_sig.model_sigs
            if old_app_sig.get_model_sig(model_sig.model_name) != model_sig
        )

        # Now do the same for models in the old signature, in case the
        # model has been deleted.
        changed_models.update(
            old_model_sig.model_name
            for old_model_sig in old_app_sig.model_sigs
            if app_sig.get_model_sig(old_model_sig.model_name) is None
        )

        # We should now have a full list of which models changed. Filter
        # the list of mutations appropriately.
        #
        # Changes affecting a model that was newly-introduced are removed,
        # unless the mutation is a RenameModel, in which case we'll need it
        # during the optimization step (and will remove it if necessary then).
        mutations = [
            mutation
            for mutation in mutations
            if (not hasattr(mutation, 'model_name') or
                mutation.model_name in changed_models or
                isinstance(mutation, RenameModel))
        ]

    return mutations


def get_app_upgrade_info(app, scan_evolutions=True, simulate_applied=False,
                         database=None):
    """Return the upgrade information to use for a given app.

    This will determine if the app should be using Django Evolution or
    Django Migrations for any schema upgrades.

    If an ``evolutions`` module is found, then this will determine the method
    to be :py:attr:`UpgradeMethod.EVOLUTIONS
    <django_evolution.consts.UpgradeMethod.EVOLUTIONS>`, unless the app has
    been moved over to using Migrations.

    If instead there's a ``migrations`` module, then this will determine
    the method to be :py:attr:`UpgradeMethod.MIGRATIONS
    <django_evolution.consts.UpgradeMethod.MIGRATIONS>`.

    Otherwise, this will return ``None``, indicating that no established
    method has been chosen. This allows a determination to be made later,
    based on the Django version or the consumer's choice.

    Note that this may return that migrations are the preferred method for
    an app even on versions of Django that do not support migrations. It's
    up to the caller to handle this however it chooses.

    Args:
        app (module):
            The app module to determine the upgrade method for.

        scan_evolutions (bool, optional):
            Whether to scan evolutions for the app to determine the current
            upgrade method.

        simulate_applied (bool, optional):
            Return the upgrade method based on the state of the app if all
            mutations had been applied. This is useful for generating end
            state signatures.

            This is ignored if passing ``scan_evolutions=False``.

        database (unicode, optional):
            The database to use for accessing stored evolution and migration
            information.

    Returns:
        dict:
        A dictionary of information containing the following keys:

        ``applied_migrations`` (list of :py:class:`unicode`):
            A list of migrations that have been applied to this app through
            any mutations. This will only be present if the upgrade method is
            set to use migrations and if running on a version of Django that
            supports migrations.

        ``has_evolutions`` (:py:class:`bool`):
            Whether there are any evolutions for this app. This may come from
            the app, project, or Django Evolution.

        ``has_migrations`` (:py:class:`bool`):
            Whether there are any migrations for this app.

        ``upgrade_method`` (:py:class:`unicode`):
            The upgrade method. This will be a value from
            :py:class:`~django_evolution.consts.UpgradeMethod`, or ``None``
            if a clear determination could not be made.
    """
    # Avoids a nasty circular import. Util modules should always be
    # importable, so we compensate here.
    from django_evolution.mutations import MoveToDjangoMigrations

    upgrade_method = None
    applied_migrations = None
    has_evolutions = has_evolutions_module(app)
    has_migrations = has_migrations_module(app)

    if has_evolutions:
        # This app made use of Django Evolution. See if we're still using
        # that, or if it's handed control over to Django migrations.
        if scan_evolutions:
            if simulate_applied:
                evolutions = None
            else:
                evolutions = get_applied_evolutions(app,
                                                    database=database)

            mutations = get_app_mutations(app=app,
                                          evolution_labels=evolutions)

            for mutation in reversed(mutations):
                if isinstance(mutation, MoveToDjangoMigrations):
                    upgrade_method = UpgradeMethod.MIGRATIONS
                    applied_migrations = mutation.mark_applied
                    break

        if not upgrade_method:
            upgrade_method = UpgradeMethod.EVOLUTIONS

    if has_migrations:
        if not upgrade_method:
            upgrade_method = UpgradeMethod.MIGRATIONS

        if supports_migrations:
            connection = connections[database or DEFAULT_DB_ALIAS]
            app_label = get_app_label(app)
            applied_migrations = \
                get_applied_migrations_by_app(connection).get(app_label)

    return {
        'applied_migrations': applied_migrations,
        'has_evolutions': has_evolutions,
        'has_migrations': has_migrations,
        'upgrade_method': upgrade_method,
    }
