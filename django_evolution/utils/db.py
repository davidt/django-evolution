"""Utility functions for database-related operations.

This provides functions for database operations, SQL generation, index name
generation, and more.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.db import connections, router
from django.db.backends.utils import names_digest, truncate_name
from django.db.utils import DEFAULT_DB_ALIAS

from django_evolution.utils.apps import get_app_label
from django_evolution.utils.models import get_models


@contextmanager
def collect_sql_schema_editor(connection):
    """Create a schema editor for the purpose of collecting SQL.

    This carefully constructs a database backend's schema editor in
    SQL-collection mode without triggering side effects that could cause
    failure when in a transaction.

    This failure mode is present on Django 2.0 and higher with SQLite.
    Essentially, it tried to disable foreign key checks and then checked if
    it succeeded in disabling those. If it did not, it would fail. This makes
    sense for execution, but not for collection.

    We work around that in this method by initializing the editor ourselves,
    setting up state, and processing the results, without invoking the
    schema editor's context management methods.

    Version Added:
        2.2

    Args:
        connection (django.db.backends.base.BaseDatabaseWrapper):
            The database connection object.

    Context:
        django.db.backends.base.schema.BaseDatabaseSchemaEditor:
        The schema editor, set up for SQL collection.

    Raises:
        Exception:
            An exception raised within the context, unmodified.
    """
    assert hasattr(connection, 'schema_editor')

    connection.disable_constraint_checking()
    schema_editor = connection.schema_editor(collect_sql=True)

    # This is normally set in DatabaseSchemaEditor.__enter__().
    schema_editor.deferred_sql = []

    # Allow exceptions to bubble up.
    yield schema_editor

    # This is normally invoked in DatabaseSchemaEditor.__exit__().
    for sql in schema_editor.deferred_sql:
        schema_editor.execute(sql)


def digest(connection, *args):
    """Return a digest hash for a set of arguments.

    This is mostly used as part of the index/constraint name generation
    processes.

    Args:
        connection (object):
            The database connection.

        *args (tuple):
            The positional arguments used to build the digest hash out of.

    Returns:
        str:
        The resulting digest hash.
    """
    return names_digest(args[0], *args[1:], length=8)


def convert_table_name(connection, name):
    """Convert a table name to a format required by the database backend.

    The conversion may result in quoting or otherwise altering the table name.

    Args:
        connection (object):
            The database connection.

        name (unicode):
            The table name to convert.

    Returns:
        unicode:
        The converted table name.
    """
    return connection.introspection.identifier_converter(name)


def sql_create_models(models, tables=None, db_name=None,
                      return_deferred=False):
    """Return SQL statements for creating a list of models.

    It's recommended that callers include auto-created models in the list,
    to ensure all references are correct.

    Version Changed:
        2.2:
        Added the ``return_deferred` argument.

    Args:
        models (list of type):
            The list of :py:class:`~django.db.models.Model` subclasses.

        tables (list of unicode, optional):
            A list of existing table names from the database. If not provided,
            this will be introspected from the database.

        db_name (str, optional):
            The database connection name. Defaults to the default database
            connection.

        return_deferred (bool, optional):
            Whether to return any deferred SQL separately from the model
            creation SQL. If ``True``, the return type will change to a tuple.

    Returns:
        list or tuple:
        If ``return_deferred=False`` (the default), this will be a list of
        SQL statements used to create the models for the app.

        If ``return_deferred=True``, this will be a 2-tuple in the form of
        ``(list_of_sql, list_of_deferred_sql)``.
    """
    connection = connections[db_name or DEFAULT_DB_ALIAS]

    with collect_sql_schema_editor(connection) as schema_editor:
        for model in models:
            schema_editor.create_model(model)

        if return_deferred:
            collected_sql = list(schema_editor.collected_sql)
            deferred_sql = [
                '%s;' % statement
                for statement in schema_editor.deferred_sql
            ]

            return collected_sql, deferred_sql

    return schema_editor.collected_sql


def sql_create_app(app, db_name=None):
    """Return SQL statements for creating all models for an app.

    Args:
        app (module):
            The application module.

        db_name (str, optional):
            The database connection name. Defaults to the default database
            connection.

    Returns:
        list:
        The list of SQL statements used to create the models for the app.
    """
    # Models for a M2M field will be created automatically, so we don't want
    # to include them in any results.
    models = get_models(app, include_auto_created=False)

    return sql_create_models(models, db_name=db_name)


def sql_delete(app, db_name=None):
    """Return SQL statements for deleting all models in an app.

    Args:
        app (module):
            The application module containing the models to delete.

        db_name (str, optional):
            The database connection name. Defaults to the default database
            connection.

    Returns:
        list:
        The list of SQL statements for deleting the models and constraints.
    """
    connection = connections[db_name or DEFAULT_DB_ALIAS]

    introspection = connection.introspection

    all_table_names = set(introspection.table_names())
    deleted_models = set()

    introspection = connection.introspection

    with collect_sql_schema_editor(connection) as schema_editor:
        for model in get_models(app):
            table_name = convert_table_name(connection,
                                            model._meta.db_table)

            if (table_name in all_table_names and
                model not in deleted_models):
                schema_editor.delete_model(model)
                deleted_models.add(model)

    return schema_editor.collected_sql


def sql_create_for_many_to_many_field(connection, model, field):
    """Return SQL statements for creating a ManyToManyField's table.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The model for the ManyToManyField's relations.

        field (django.db.models.ManyToManyField):
            The field setting up the many-to-many relation.

    Returns:
        list:
        The list of SQL statements for creating the table and constraints.
    """
    through = field.remote_field.through

    with collect_sql_schema_editor(connection) as schema_editor:
        schema_editor.create_model(through)

    return schema_editor.collected_sql


def sql_indexes_for_field(connection, model, field):
    """Return SQL statements for creating indexes for a field.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model owning the field.

        field (django.db.models.Field):
            The field being indexed.

    Returns:
        list:
        The list of SQL statements for creating the indexes.
    """
    # Unlike sql_indexes_for_field(), _create_index_sql() won't be
    # checking whether it *should* create an index for the given field.
    # We have to check that here instead.
    if not field.db_index or field.unique:
        return []

    with collect_sql_schema_editor(connection) as schema_editor:
        return ['%s;' % schema_editor._create_index_sql(model,
                                                        fields=[field])]


def sql_indexes_for_fields(connection, model, fields, index_together=False):
    """Return SQL statements for creating indexes covering multiple fields.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model owning the fields.

        fields (list of django.db.models.Field):
            The list of fields for the index.

        index_together (bool, optional):
            Whether this is from an index_together rule.

    Returns:
        list:
        The list of SQL statements for creating the indexes.
    """
    if index_together:
        suffix = '_idx'
    else:
        suffix = ''

    with collect_sql_schema_editor(connection) as schema_editor:
        return ['%s;' % schema_editor._create_index_sql(model,
                                                        fields=fields,
                                                        suffix=suffix)]


def sql_indexes_for_model(connection, model):
    """Return SQL statements for creating all indexes for a model.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model to create indexes for.

    Returns:
        list:
        The list of SQL statements for creating the indexes.
    """
    with collect_sql_schema_editor(connection) as schema_editor:
        return [
            '%s;' % s
            for s in schema_editor._model_indexes_sql(model)
        ]


def sql_delete_index(connection, model, index_name):
    """Return SQL statements for deleting an index.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model to delete an index on.

        index_name (unicode):
            The name of the index to delete.

    Returns:
        list:
        The list of SQL statements for deleting the index.
    """
    with collect_sql_schema_editor(connection) as schema_editor:
        return [
            '%s;' % schema_editor._delete_constraint_sql(
                template=schema_editor.sql_delete_index,
                model=model,
                name=index_name),
        ]


def sql_delete_constraints(connection, model, remove_refs):
    """Return SQL statements for deleting constraints.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model to delete constraints on.

        remove_refs (dict):
            A dictionary of constraint references to remove.

            The keys are instances of :py:class:`django.db.models.Model`.
            The values are a tuple of (:py:class:`django.db.models.Model`,
            :py:class:`django.db.models.Field`).

            Warning:
                Keys may be removed as constraints are deleted. Make sure to
                pass in a copy of the dictionary if the original dictionary
                msut be preserved.

    Returns:
        list:
        The list of SQL statements for deleting constraints.
    """
    meta = model._meta

    if not meta.managed or meta.swapped or meta.proxy:
        return []

    sql = []

    with collect_sql_schema_editor(connection) as schema_editor:
        for rel_class, f in remove_refs[model]:
            fk_names = schema_editor._constraint_names(
                rel_class, [f.column], foreign_key=True)

            for fk_name in fk_names:
                sql.append('%s;' % schema_editor._delete_constraint_sql(
                    schema_editor.sql_delete_fk, rel_class, fk_name))

    return sql


def sql_add_constraints(connection, model, refs):
    """Return SQL statements for adding constraints.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model to add constraints on.

        refs (dict):
            A dictionary of constraint references to add.

            The keys are instances of :py:class:`django.db.models.Model`.
            The values are a tuple of (:py:class:`django.db.models.Model`,
            :py:class:`django.db.models.Field`).

            Warning:
                Keys may be removed as constraints are added. Make sure to
                pass in a copy of the dictionary if the original dictionary
                msut be preserved.

    Returns:
        list:
        The list of SQL statements for adding constraints.
    """
    meta = model._meta

    if not meta.managed or meta.swapped:
        return []

    sql = []

    if model in refs:
        with collect_sql_schema_editor(connection) as schema_editor:
            assert schema_editor.sql_create_fk, (
                'sql_add_constraints() cannot be called for this type '
                'of database.'
            )

            qn = schema_editor.quote_name

            for rel_class, f in refs[model]:
                # Ideally, we would use schema_editor._create_fk_sql here,
                # but it depends on a lot more state than we have
                # available currently in our mocks. So we have to build
                # the SQL ourselves. It's not a lot of work, fortunately.
                #
                # For reference, this is what we'd ideally do:
                #
                #     sql.append('%s;' % schema_editor._create_fk_sql(
                #         rel_class, f,
                #         '_fk_%(to_table)s_%(to_column)s'))
                #
                rel_meta = rel_class._meta
                to_column = (
                    meta.get_field(f.remote_field.field_name)
                    .column
                )

                suffix = '_fk_%(to_table)s_%(to_column)s' % {
                    'to_table': meta.db_table,
                    'to_column': to_column,
                }

                name = create_index_name(connection=connection,
                                         table_name=rel_meta.db_table,
                                         col_names=[f.column],
                                         suffix=suffix)

                create_sql = schema_editor.sql_create_fk % {
                    'table': qn(rel_meta.db_table),
                    'name': qn(name),
                    'column': qn(f.column),
                    'to_table': qn(meta.db_table),
                    'to_column': qn(to_column),
                    'deferrable': connection.ops.deferrable_sql(),
                }

                sql.append('%s;' % create_sql)

        del refs[model]

    return sql


def create_index_name(connection, table_name, field_names=[], col_names=[],
                      unique=False, suffix=''):
    """Return the name for an index for a field.

    Args:
        connection (object):
            The database connection.

        table_name (str):
            The name of the table.

        field_names (list of str, optional):
            The list of field names for the index.

        col_names (list of str, optional):
            The list of column names for the index.

        unique (bool, optional):
            Whether or not this index is unique.

        suffix (str, optional):
            A suffix for the index. This is only used with Django >= 1.7.

    Returns:
        str:
        The generated index name for this version of Django.
    """
    if unique:
        assert not suffix
        suffix = '_uniq'

    with collect_sql_schema_editor(connection) as schema_editor:
        return schema_editor._create_index_name(table_name,
                                                col_names or field_names,
                                                suffix=suffix)


def create_index_together_name(connection, table_name, field_names):
    """Return the name of an index for an index_together.

    Args:
        connection (object):
            The database connection.

        table_name (str):
            The name of the table.

        field_names (list of str):
            The list of field names indexed together.

    Returns:
        str:
        The generated index name for this version of Django.
    """
    # Starting in 1.7, the index_together indexes were given a "_idx"
    # suffix.
    return create_index_name(connection, table_name, field_names,
                             field_names, suffix='_idx')


def create_constraint_name(connection, r_col, col, r_table, table):
    """Return the name of a constraint.

    Args:
        connection (object):
            The database connection.

        r_col (str):
            The column name for the source of the relation.

        col (str):
            The column name for the "to" end of the relation.

        r_table (str):
            The table name for the source of the relation.

        table (str):
            The table name for the "to" end of the relation.

    Returns:
        str:
        The generated constraint name for this version of Django.
    """
    suffix = '_fk_%(to_table)s_%(to_column)s' % {
        'to_table': table,
        'to_column': col,
    }

    # No need to truncate here, since create_index_name() will do it for
    # us.
    return create_index_name(connection, r_table, col_names=[r_col],
                             suffix=suffix)


def db_router_allows_schema_upgrade(database, app_label, model_cls):
    """Return whether a database router allows a schema upgrade for a model.

    This is a convenience wrapper around :py:func:`db_router_allows_migrate`.

    Args:
        database (unicode):
            The name of the database.

        app_label (unicode):
            The application label.

        model_cls (type):
            The model class.

    Returns:
        bool:
        ``True`` if routers allow migrate for this model.
    """
    return router.allow_migrate_model(database, model_cls)


def db_get_installable_models_for_app(app, db_state):
    """Return models that can be installed in a database.

    Args:
        app (module):
            The models module for the app.

        db_state (django_evolution.db.state.DatabaseState):
            The introspected state of the database.
    """
    app_label = get_app_label(app)

    # Models for a M2M field will be created automatically, so we don't want
    # to include them in any results.
    return [
        model
        for model in get_models(app, include_auto_created=False)
        if (not db_state.has_model(model) and
            db_router_allows_schema_upgrade(db_state.db_name, app_label,
                                            model))
    ]


__all__ = [
    'create_constraint_name',
    'create_index_name',
    'create_index_together_name',
    'db_get_installable_models_for_app',
    'db_router_allows_schema_upgrade',
    'digest',
    'sql_add_constraints',
    'sql_create_app',
    'sql_create_models',
    'sql_create_for_many_to_many_field',
    'sql_delete',
    'sql_delete_constraints',
    'sql_delete_index',
    'sql_indexes_for_field',
    'sql_indexes_for_fields',
    'sql_indexes_for_model',
    'truncate_name',
]
