from __future__ import unicode_literals

import logging
from contextlib import contextmanager
from functools import partial

import django
from django.conf import settings
from django.db import connections
from django.db.utils import ConnectionHandler, DEFAULT_DB_ALIAS
from django.utils import six

from django_evolution.compat.apps import (is_app_registered, register_app,
                                          register_app_models,
                                          unregister_app_model)
from django_evolution.compat.datastructures import OrderedDict
from django_evolution.compat.db import (atomic, create_index_name,
                                        create_index_together_name, digest,
                                        sql_create_app, sql_delete,
                                        truncate_name)
from django_evolution.compat.models import (all_models,
                                            get_model_name,
                                            get_remote_field,
                                            get_remote_field_model,
                                            set_model_name)
from django_evolution.db import EvolutionOperationsMulti
from django_evolution.signature import (AppSignature, ModelSignature,
                                        ProjectSignature)
from django_evolution.tests import models as evo_test
from django_evolution.utils.sql import execute_sql, write_sql


test_connections = ConnectionHandler(settings.TEST_DATABASES)


def register_models(database_state, models, register_indexes=False,
                    new_app_label='tests', db_name='default', app=evo_test):
    """Register models for testing purposes.

    Args:
        database_state (django_evolution.db.state.DatabaseState):
            The database state to populate with model information.

        models (list of django.db.models.Model):
            The models to register.

        register_indexes (bool, optional):
            Whether indexes should be registered for any models. Defaults to
            ``False``.

        new_app_label (str, optional):
            The label for the test app. Defaults to "tests".

        db_name (str, optional):
            The name of the database connection. Defaults to "default".

        app (module, optional):
            The application module for the test models.

    Returns:
        collections.OrderedDict:
        A dictionary of registered models. The keys are model names, and
        the values are the models.
    """
    app_cache = OrderedDict()
    evolver = EvolutionOperationsMulti(db_name, database_state).get_evolver()

    db_connection = connections[db_name or DEFAULT_DB_ALIAS]
    max_name_length = db_connection.ops.max_name_length()

    for new_object_name, model in reversed(models):
        # Grab some state from the model's meta instance. Some of this will
        # be original state that we'll keep around to help us unregister old
        # values and compute new ones.
        meta = model._meta

        orig_app_label = meta.app_label
        orig_db_table = meta.db_table
        orig_object_name = meta.object_name
        orig_model_name = get_model_name(model)

        # Find out if the table name being used is a custom table name, or
        # one generated by Django.
        new_model_name = new_object_name.lower()
        new_db_table = orig_db_table

        generated_db_table = truncate_name(
            '%s_%s' % (orig_app_label, orig_model_name),
            max_name_length)

        if orig_db_table == generated_db_table:
            # It was a generated one, so replace it with a version containing
            # the new model and app names.
            new_db_table = truncate_name('%s_%s' % (new_app_label,
                                                    new_model_name),
                                         max_name_length)
            meta.db_table = new_db_table

        # Set the new app/model names back on the meta instance.
        meta.app_label = new_app_label
        meta.object_name = new_object_name
        set_model_name(model, new_model_name)

        # Add an entry for the table in the database state, if it's not
        # already there.
        if not database_state.has_table(new_db_table):
            database_state.add_table(new_db_table)

        if register_indexes:
            # Now that we definitely have an entry, store the indexes for
            # all the fields in the database state, so that other operations
            # can look up the index names.
            for field in meta.local_fields:
                if field.db_index or field.unique:
                    new_index_name = create_index_name(
                        db_connection,
                        new_db_table,
                        field_names=[field.name],
                        col_names=[field.column],
                        unique=field.unique)

                    database_state.add_index(
                        index_name=new_index_name,
                        table_name=new_db_table,
                        columns=[field.column],
                        unique=field.unique)

            for field_names in meta.unique_together:
                fields = evolver.get_fields_for_names(model, field_names)
                new_index_name = create_index_name(
                    db_connection,
                    new_db_table,
                    field_names=field_names,
                    unique=True)

                database_state.add_index(
                    index_name=new_index_name,
                    table_name=new_db_table,
                    columns=[field.column for field in fields],
                    unique=True)

            for field_names in getattr(meta, 'index_together', []):
                # Django >= 1.5
                fields = evolver.get_fields_for_names(model, field_names)
                new_index_name = create_index_together_name(
                    db_connection,
                    new_db_table,
                    field_names=[field.name for field in fields])

                database_state.add_index(
                    index_name=new_index_name,
                    table_name=new_db_table,
                    columns=[field.column for field in fields])

            if getattr(meta, 'indexes', None):
                # Django >= 1.11
                for index, orig_index in zip(meta.indexes,
                                             meta.original_attrs['indexes']):
                    if not orig_index.name:
                        # The name was auto-generated. We'll need to generate
                        # it again for the new table name.
                        index.set_name_with_model(model)

                    fields = evolver.get_fields_for_names(
                        model, index.fields, allow_sort_prefixes=True)
                    database_state.add_index(
                        index_name=index.name,
                        table_name=new_db_table,
                        columns=[field.column for field in fields])

        # ManyToManyFields have their own tables, which will also need to be
        # renamed. Go through each of them and figure out what changes need
        # to be made.
        for field in meta.local_many_to_many:
            through = get_remote_field(field).through

            if not through:
                continue

            through_meta = through._meta
            through_orig_model_name = get_model_name(through)
            through_new_model_name = through_orig_model_name

            # Find out if the through table name is a custom table name, or
            # one generated by Django.
            generated_db_table = truncate_name(
                '%s_%s' % (orig_db_table, field.name),
                max_name_length)

            if through_meta.db_table == generated_db_table:
                # This is an auto-generated table name. Start changing the
                # state for it.
                assert through_meta.app_label == orig_app_label
                through_meta.app_label = new_app_label

                # Transform the 'through' table information only if we've
                # transformed the parent db_table.
                if new_db_table != orig_db_table:
                    through_meta.db_table = truncate_name(
                        '%s_%s' % (new_db_table, field.name),
                        max_name_length)

                    through_meta.object_name = \
                        through_meta.object_name.replace(orig_object_name,
                                                         new_object_name)

                    through_new_model_name = \
                        through_orig_model_name.replace(orig_model_name,
                                                        new_model_name)
                    set_model_name(through, through_new_model_name)

            # Change each of the columns for the fields on the
            # ManyToManyField's model to reflect the new model names.
            for through_field in through._meta.local_fields:
                through_remote_field = get_remote_field(through_field)

                if (through_remote_field and
                    get_remote_field_model(through_remote_field)):
                    column = through_field.column

                    if (column.startswith((orig_model_name,
                                           'to_%s' % orig_model_name,
                                           'from_%s' % orig_model_name))):
                        # This is a field that references one end of the
                        # relation or another. Update the model naem in the
                        # field's column.
                        through_field.column = column.replace(orig_model_name,
                                                              new_model_name)

            # Replace the entry in the models cache for the through table,
            # removing the old name and adding the new one.
            if through_orig_model_name in all_models[orig_app_label]:
                unregister_app_model(orig_app_label, through_orig_model_name)

            app_cache[through_new_model_name] = through
            register_app_models(new_app_label,
                                [(through_new_model_name, through)])

        # Unregister with the old model name and register the new one.
        if orig_model_name in all_models[orig_app_label]:
            unregister_app_model(orig_app_label, orig_model_name)

        register_app_models(new_app_label, [(new_model_name, model)])
        app_cache[new_model_name] = model

    # If the app hasn't yet been registered, do that now.
    if not is_app_registered(app):
        register_app(new_app_label, app)

    return app_cache


def create_test_project_sig(models, app_label='tests', version=1):
    """Return a dummy project signature for the given models.

    Args:
        models (list of django.db.models.Model):
            The list of models for the project signature.

        app_label (unicode, optional):
            The application label that will contain the models.

        version (int, optional):
            The signature version to use for the project signature.

    Returns:
        dict:
        The new project signature.
    """
    app_sig = AppSignature(app_id=app_label)

    project_sig = ProjectSignature()
    project_sig.add_app_sig(app_sig)

    for full_name, model in models:
        parts = full_name.split('.')

        if len(parts) == 1:
            app_sig.add_model_sig(ModelSignature.from_model(model))
        else:
            model_app_label, model_name = parts
            model_app_sig = project_sig.get_app_sig(model_app_label)

            if model_app_sig is None:
                model_app_sig = AppSignature(app_id=model_app_label)
                project_sig.add_app_sig(model_app_sig)

            model_app_sig.add_model_sig(ModelSignature.from_model(model))

    return project_sig


def execute_transaction(sql, database=DEFAULT_DB_ALIAS):
    """Execute SQL in a new transaction.

    Args:
        sql (unicode or list):
            The SQL to execute. This must be a value accepted by
            :py:func:`~django_evolution.utils.execute_sql`.

        database (unicode, optional):
            The name of the database to use.
    """
    connection = connections[database]

    try:
        with connection.constraint_checks_disabled():
            with atomic(using=database):
                execute_sql(connection.cursor(), sql, database)
    except Exception as e:
        logging.exception('Error executing SQL %s: %s', sql, e)
        raise


@contextmanager
def ensure_test_db(model_entries=[], app_label='tests',
                   database=DEFAULT_DB_ALIAS):
    """Ensure tables are created and destroyed when running code.

    This will register all necessary models and indexes, provided by the
    caller, and populate them in the database. After the inner context has
    completed, the models and indexes will be destroyed.

    Args:
        model_entries (list of tuple, optional):
            The list of model entries to add to the database. Each entry
            is a tuple containing the model class and the name to register
            for it.

        app_label (unicode, optional):
            The application label for the models to register.

        database (unicode, optional):
            The name of the database to execute on.
    """
    # Set up the initial state of the app cache.
    if model_entries:
        register_app_models(app_label, model_entries, reset=True)

        # Install the initial tables and indexes.
        execute_transaction(sql_create_app(app=evo_test,
                                           db_name=database),
                            database)

    try:
        yield
    finally:
        # Clean up the database.
        execute_transaction(sql_delete(evo_test, database),
                            database)


def execute_test_sql(start_sig, end_sig, generate_sql_func, app_label='tests',
                     database=DEFAULT_DB_ALIAS):
    """Execute SQL for a unit test.

    This will register all necessary models and indexes, as defined by the
    starting signature, and populate them in the database. It then sets the
    model state to reflect the ending signature, allowing the unit test to
    perform operations to go from the in-database starting state to the
    new ending signature state.

    The SQL provided by ``generate_sql_func`` will be output to the console,
    to aid in debugging when tests fail.

    Args:
        start_sig (dict):
            The signature for the initial database state, used to generate
            tables and indexes in the database.

        end_sig (dict):
            The signature for the ending database state, reflecting what the
            evolutions will be attempting to evolve to.

        generate_sql_func (callable):
            A function that takes no parameters and returns SQL to execute,
            once the database and app/model states are set up.

        app_label (unicode, optional):
            The application label for any models contained in the signature.

        database (unicode, optional):
            The name of the database to execute on.

    Returns:
        list of unicode:
        The list of executed SQL statements for the test.
    """
    with ensure_test_db(model_entries=six.iteritems(start_sig),
                        app_label=app_label,
                        database=database):
        # Set the app cache to the end state. generate_sql will depend on
        # this state.
        register_app_models(app_label, six.iteritems(end_sig), reset=True)

        # Execute and output the SQL for the test.
        sql = generate_sql_func()
        sql_out = write_sql(sql, database)
        execute_transaction(sql, database)

        return sql_out


def get_sql_mappings(mapping_key, db_name):
    """Return the test SQL mappings dictionary for the current database type.

    The mappings contain SQL statements that are used to test executed SQL
    against.

    Args:
        mapping_key (unicode):
            The mapping key in the module. This must correspond to a SQL
            statements dictionary.

        db_name (unicode):
            The name of the database.

    Returns:
        dict:
        The mappings dictionary for the given mapping key and database.
    """
    engine = settings.DATABASES[db_name]['ENGINE'].split('.')[-1]

    # Convert alternative database names to their proper test data module.
    engine = {
        'postgresql_psycopg2': 'postgresql',
    }.get(engine, engine)

    sql_for_engine = __import__('django_evolution.tests.db.%s' % engine,
                                {}, {}, [''])

    return getattr(sql_for_engine, mapping_key)


def generate_index_name(connection, table, col_names, field_names=None,
                        index_together=False, model_meta_indexes=False):
    """Generate a suitable index name to test against.

    The returned index name is meant for use in the test data modules, and
    is used to compare our own expectations of how an index should be named
    with the naming Django provides in its own functions.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

        table (unicode):
            The name of the table the index refers to.

        col_names (unicode or list of unicode):
            The column name, or list of column names, for the index.

            This is used for Postgres (when not using ``index_together``),
            or for Django < 1.5. Otherwise, it's interchangeable with
            ``field_names``.

        field_names (str or list of str, optional):
            The field name, or list of field names, for the index.

            This is interchangeable with ``column_names`` on Django >= 1.5
            (unless using Postgres without ``index_together``), or when
            passing ``default=True``.

        index_together (bool, optional):
            Whether this index covers multiple fields indexed together
            through Django's ``Model._meta.index_together``.

            Defaults to ``False``.

        model_meta_indexes (bool, optional):
            The index comes from a
            :py:class:`django.db.models.Options.indexes` entry.

    Returns:
        unicode:
        The resulting index name for the given criteria.
    """
    if not isinstance(col_names, list):
        col_names = [col_names]

    if field_names and not isinstance(field_names, list):
        field_names = [field_names]

    if not field_names:
        field_names = col_names

    assert len(field_names) == len(col_names)

    django_version = django.VERSION[:2]

    # Note that we're checking Django versions/engines specifically, since
    # we want to test that we're getting the right index names for the
    # right versions of Django, rather than asking Django for them.
    #
    # The order here matters.
    if django_version >= (1, 11):
        # Django 1.11+ changed the index format again, this time to include
        # all relevant column names in the plain text part of the index
        # (instead of just in the hash). Like with 1.7 through 1.10, the
        # index_together entries have a "_idx" suffix. However, there's
        # otherwise no difference in format between those and single-column
        # indexes.
        #
        # It's also worth noting that with the introduction of
        # Model._meta.indexes, there's *another* new index format. It's
        # similar, but different enough, and needs to be handled specially.
        if model_meta_indexes:
            name = '%s_%s' % (
                col_names[0][:7],
                digest(connection, *([table] + col_names + ['idx']))[:6],
            )
            table = table[:11]
        else:
            index_unique_name = _generate_index_unique_name_hash(
                connection, table, col_names)
            name = '%s_%s' % ('_'.join(col_names), index_unique_name)

        if model_meta_indexes or index_together:
            name = '%s_idx' % name
    elif django_version >= (1, 7):
        if len(col_names) == 1:
            assert not index_together

            # Django 1.7 went back to passing a single column name (and
            # not a list as a single variable argument) when there's only
            # one column.
            name = digest(connection, col_names[0])
        else:
            assert index_together

            index_unique_name = _generate_index_unique_name_hash(
                connection, table, col_names)
            name = '%s_%s_idx' % (col_names[0], index_unique_name)
    elif connection.vendor == 'postgresql' and not index_together:
        # Postgres computes the index names separately from the rest of
        # the engines. It just uses '<tablename>_<colname>", same as
        # Django < 1.2. We only do this for normal indexes, though, not
        # index_together.
        name = col_names[0]
    elif django_version >= (1, 5):
        # Django >= 1.5 computed the digest of the representation of a
        # list of either field names or column names. Note that digest()
        # takes variable positional arguments, which this is not passing.
        # This is due to a design bug in these versions.
        #
        # We convert each of the field names to Python's native string
        # format, which is what the default name would normally be in.
        name = digest(connection, [
            str(field_name)
            for field_name in (field_names or col_names)
        ])
    elif django_version >= (1, 2):
        # Django >= 1.2, < 1.7 used the digest of the name of the first
        # column. There was no index_together in these releases.
        name = digest(connection, col_names[0])
    else:
        # Django < 1.2 used just the name of the first column, no digest.
        name = col_names[0]

    return truncate_name('%s_%s' % (table, name),
                         connection.ops.max_name_length())


def make_generate_index_name(connection):
    """Return an index generation function for the given database type.

    This is used by the test data modules as a convenience to allow
    for a local version of :py:func:`generate_index_name` that doesn't need
    to be passed a database connection on every call.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

    Returns:
        callable:
        A version of :py:func:`generate_index_name` that doesn't need the
        ``db_type`` parameter.
    """
    return partial(generate_index_name, connection)


def generate_constraint_name(connection, r_col, col, r_table, table):
    """Return the expected name for a constraint.

    This will generate a constraint name for the current version of Django,
    for comparison purposes.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

        r_col (unicode):
            The column name for the source of the relation.

        col (unicode):
            The column name for the "to" end of the relation.

        r_table (unicode):
            The table name for the source of the relation.

        table (unicode):
            The table name for the "to" end of the relation.

    Returns:
        unicode:
        The expected name for a constraint.
    """
    django_version = django.VERSION[:2]

    if django_version >= (1, 11):
        # Django 1.11 changed how index names are generated and then
        # shortened, choosing to shorten more preemptively. This does impact
        # the tests, so we need to be sure to get the logic right.
        max_length = connection.ops.max_name_length() or 200
        index_unique_name = _generate_index_unique_name_hash(
            connection, r_table, [r_col])
        suffix = '%s_fk_%s_%s' % (index_unique_name, table, col)
        full_name = '%s_%s_%s' % (r_table, r_col, suffix)

        if len(full_name) > max_length:
            if len(suffix) > (max_length // 3):
                suffix = suffix[:max_length // 3]

            part_lengths = (max_length - len(suffix)) // 2 - 1
            full_name = '%s_%s_%s' % (r_table[:part_lengths],
                                      r_col[:part_lengths],
                                      suffix)

        return full_name
    elif django_version >= (1, 7):
        # This is an approximation of what Django 1.7+ uses for constraint
        # naming. It's actually the same as index naming, but for test
        # purposes, we want to keep this distinct from the index naming above.
        # It also doesn't cover all the cases that
        # BaseDatabaseSchemaEditor._create_index_name covers, but they're not
        # necessary for our tests (and we'll know if it all blows up somehow).
        max_length = connection.ops.max_name_length() or 200
        index_unique_name = _generate_index_unique_name_hash(
            connection, r_table, [r_col])

        name = '_%s_%s_fk_%s_%s' % (r_col, index_unique_name, table, col)
        full_name = '%s%s' % (r_table, name)

        if len(full_name) > max_length:
            full_name = '%s%s' % (r_table[:(max_length - len(name))], name)

        return full_name
    else:
        return '%s_refs_%s_%s' % (r_col, col,
                                  digest(connection, r_table, table))


def make_generate_constraint_name(connection):
    """Return a constraint generation function for the given database type.

    This is used by the test data modules as a convenience to allow
    for a local version of :py:func:`generate_constraint_name` that doesn't
    need to be passed a database connection on every call.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

    Returns:
        callable:
        A version of :py:func:`generate_constraint_name` that doesn't need the
        ``db_type`` parameter.
    """
    return partial(generate_constraint_name, connection)


def generate_unique_constraint_name(connection, table, col_names):
    """Return the expected name for a unique constraint.

    This will generate a constraint name for the current version of Django,
    for comparison purposes.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

        table (unicode):
            The table name.

        col_names (list of unicode):
            The list of column names for the constraint.

    Returns:
        unicode:
        The expected constraint name for this version of Django.
    """
    django_version = django.VERSION[:2]

    if django_version >= (1, 11):
        # Django 1.11 changed how index names are generated and then
        # shortened, choosing to shorten more preemptively. This does impact
        # the tests, so we need to be sure to get the logic right.
        max_length = connection.ops.max_name_length() or 200
        index_unique_name = _generate_index_unique_name_hash(
            connection, table, col_names)

        suffix = '%s_uniq' % index_unique_name
        col_names_part = '_'.join(col_names)
        full_name = '%s_%s_%s' % (table, col_names_part, suffix)

        if len(full_name) > max_length:
            if len(suffix) > (max_length // 3):
                suffix = suffix[:max_length // 3]

            part_lengths = (max_length - len(suffix)) // 2 - 1
            full_name = '%s_%s_%s' % (table[:part_lengths],
                                      col_names_part[:part_lengths],
                                      suffix)

        return full_name
    elif django_version >= (1, 7):
        # Django versions >= 1.7 all use roughly the same format for unique
        # constraint index names, but starting in Django 1.11, the format
        # changed slightly. In 1.7 through 1.10, the name contained only the
        # first column (if specifying more than one), but in 1.11, that
        # changed to contain all column names (for unique_together).
        max_length = connection.ops.max_name_length() or 200
        index_unique_name = _generate_index_unique_name_hash(
            connection, table, col_names)

        name = '_%s_%s_uniq' % (col_names[0], index_unique_name)
        full_name = '%s%s' % (table, name)

        if len(full_name) > max_length:
            full_name = '%s%s' % (table[:(max_length - len(name))], name)

        return full_name
    else:
        # Convert each of the field names to Python's native string format,
        # which is what the default name would normally be in.
        name = digest(connection, [
            str(col_name)
            for col_name in col_names
        ])

        return truncate_name('%s_%s' % (table, name),
                             connection.ops.max_name_length())


def make_generate_unique_constraint_name(connection):
    """Return a constraint generation function for the given database type.

    This is used by the test data modules as a convenience to allow
    for a local version of :py:func:`generate_constraint_name` that doesn't
    need to be passed a database connection on every call.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

    Returns:
        callable:
        A version of :py:func:`generate_constraint_name` that doesn't need the
        ``db_type`` parameter.
    """
    return partial(generate_unique_constraint_name, connection)


def _generate_index_unique_name_hash(connection, table, col_names):
    """Return the hash for the unique part of an index name.

    Args:
        connection (django.db.backends.base.base.BaseDatabaseWrapper):
            The database connection.

        table (unicode):
            The name of the table.

        col_names (list of unicode):
            The list of column names for the index.

    Returns:
        unicode:
        A hash for the unique part of an index.
    """
    assert isinstance(col_names, list)

    if django.VERSION[:2] >= (1, 9):
        # Django >= 1.9
        #
        # Django 1.9 introduced a new format for the unique index hashes,
        # switching back to using digest() instead of hash().
        return digest(connection, table, *col_names)
    else:
        # Django >= 1.7, < 1.9
        return '%x' % abs(hash((table, ','.join(col_names))))
