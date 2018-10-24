"""Compatibility functions for database-related operations.

This provides functions for database operations, SQL generation, index name
generation, and more. These functions translate to the various versions of
Django that are supported.
"""

from __future__ import unicode_literals

from contextlib import contextmanager

import django
from django.core.management import color, sql
from django.db import connections, router, transaction
from django.db.utils import DEFAULT_DB_ALIAS
from django.utils import six

try:
    # Django >= 1.7
    from django.apps.registry import apps
    from django.db.backends.utils import truncate_name
    from django.db.migrations.executor import MigrationExecutor
except ImportError:
    # Django < 1.7
    from django.db.backends.util import truncate_name

    apps = None
    MigrationExecutor = None

try:
    # Django >= 1.8
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
except ImportError:
    try:
        # Django == 1.7
        from django.db.backends.schema import BaseDatabaseSchemaEditor
    except ImportError:
        # Django < 1.7
        BaseDatabaseSchemaEditor = None

from django_evolution.compat.models import get_models, get_remote_field
from django_evolution.support import supports_index_together


@contextmanager
def atomic(using=None):
    """Perform database operations atomically within a transaction.

    The caller can use this to ensure SQL statements are executed within
    a transaction and then cleaned up nicely if there's an error.

    This provides compatibility with all supported versions of Django.

    Args:
        using (str, optional):
            The database connection name to use. Defaults to the default
            database connection.
    """
    if hasattr(transaction, 'atomic'):
        # Django >= 1.5
        with transaction.atomic(using=using):
            yield
    else:
        # Django < 1.5
        assert hasattr(transaction, 'enter_transaction_management')

        try:
            # Begin Transaction
            transaction.enter_transaction_management(using=using)
            transaction.managed(True, using=using)

            yield

            transaction.commit(using=using)
            transaction.leave_transaction_management(using=using)
        except Exception:
            transaction.rollback(using=using)
            raise


def digest(connection, *args):
    """Return a digest hash for a set of arguments.

    This is mostly used as part of the index/constraint name generation
    processes. It offers compatibility with a range of Django versions.

    Args:
        connection (object):
            The database connection.

        *args (tuple):
            The positional arguments used to build the digest hash out of.

    Returns:
        str:
        The resulting digest hash.
    """
    if (BaseDatabaseSchemaEditor and
        hasattr(BaseDatabaseSchemaEditor, '_digest')):
        # Django >= 1.8
        #
        # Note that _digest() is a classmethod that is common across all
        # database backends. We don't need to worry about using a
        # per-instance version. If that changes, we'll need to create a
        # SchemaEditor.
        return BaseDatabaseSchemaEditor._digest(*args)
    else:
        # Django < 1.8
        return connection.creation._digest(*args)


def sql_create(app, db_name=None):
    """Return SQL statements for creating all models for an app.

    This provides compatibility with all supported versions of Django.

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
    connection = connections[db_name or DEFAULT_DB_ALIAS]

    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        with connection.schema_editor(collect_sql=True) as schema_editor:
            for model in get_models(app):
                schema_editor.create_model(model)

        return schema_editor.collected_sql
    else:
        # Django < 1.7
        style = color.no_style()

        return (sql.sql_create(app, style, connection) +
                sql.sql_indexes(app, style, connection))


def sql_delete(app, db_name=None):
    """Return SQL statements for deleting all models in an app.

    This provides compatibility with all supported versions of Django.

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

    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        all_table_names = set(connection.introspection.table_names())
        deleted_models = set()

        introspection = connection.introspection

        with connection.schema_editor(collect_sql=True) as schema_editor:
            for model in get_models(app):
                table_name = introspection.table_name_converter(
                    model._meta.db_table)

                if (table_name in all_table_names and
                    model not in deleted_models):
                    schema_editor.delete_model(model)
                    deleted_models.add(model)

        return schema_editor.collected_sql
    else:
        # Django < 1.7
        style = color.no_style()

        return sql.sql_delete(app, style, connection)


def sql_create_for_many_to_many_field(connection, model, field):
    """Return SQL statements for creating a ManyToManyField's table.

    This provides compatibility with all supported versions of Django.

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
    through = get_remote_field(field).through

    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        with connection.schema_editor(collect_sql=True) as schema_editor:
            schema_editor.create_model(through)

        return schema_editor.collected_sql
    else:
        # Django < 1.7
        style = color.no_style()

        if field.rel.through:
            references = {}
            pending_references = {}

            sql, references = connection.creation.sql_create_model(
                field.rel.through, style)

            # Sort the list, in order to create consistency in the order of
            # ALTER TABLEs. This is primarily needed for unit tests.
            for refto, refs in sorted(six.iteritems(references),
                                      key=lambda i: repr(i)):
                pending_references.setdefault(refto, []).extend(refs)
                sql.extend(sql_add_constraints(connection, refto,
                                               pending_references))

            sql.extend(sql_add_constraints(connection, field.rel.through,
                                           pending_references))
        else:
            sql = connection.creation.sql_for_many_to_many_field(
                model, field, style)

        return sql


def sql_indexes_for_field(connection, model, field):
    """Return SQL statements for creating indexes for a field.

    This provides compatibility with all supported versions of Django.

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
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        #
        # Unlike sql_indexes_for_field(), _create_index_sql() won't be
        # checking whether it *should* create an index for the given field.
        # We have to check that here instead.
        if not field.db_index or field.unique:
            return []

        with connection.schema_editor() as schema_editor:
            return ['%s;' % schema_editor._create_index_sql(model, [field])]
    else:
        # Django < 1.7
        return connection.creation.sql_indexes_for_field(model, field,
                                                         color.no_style())


def sql_indexes_for_fields(connection, model, fields, index_together=False):
    """Return SQL statements for creating indexes covering multiple fields.

    This provides compatibility with all supported versions of Django.

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
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        if index_together:
            suffix = '_idx'
        else:
            suffix = ''

        with connection.schema_editor() as schema_editor:
            return ['%s;' % schema_editor._create_index_sql(model, fields,
                                                            suffix=suffix)]
    else:
        # Django < 1.7
        return connection.creation.sql_indexes_for_fields(model, fields,
                                                          color.no_style())


def sql_indexes_for_model(connection, model):
    """Return SQL statements for creating all indexes for a model.

    This provides compatibility with all supported versions of Django.

    Args:
        connection (object):
            The database connection.

        model (django.db.models.Model):
            The database model to create indexes for.

    Returns:
        list:
        The list of SQL statements for creating the indexes.
    """
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        with connection.schema_editor() as schema_editor:
            return [
                '%s;' % s
                for s in schema_editor._model_indexes_sql(model)
            ]
    else:
        # Django < 1.7
        return connection.creation.sql_indexes_for_model(model,
                                                         color.no_style())


def sql_delete_constraints(connection, model, remove_refs):
    """Return SQL statements for deleting constraints.

    This provides compatibility with all supported versions of Django.

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

    Returns:
        list:
        The list of SQL statements for deleting constraints.
    """
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        meta = model._meta

        if not meta.managed or meta.swapped or meta.proxy:
            return []

        sql = []

        with connection.schema_editor() as schema_editor:
            for rel_class, f in remove_refs[model]:
                fk_names = schema_editor._constraint_names(
                    rel_class, [f.column], foreign_key=True)

                for fk_name in fk_names:
                    sql.append('%s;' % schema_editor._delete_constraint_sql(
                        schema_editor.sql_delete_fk, rel_class, fk_name))

        return sql
    else:
        # Django < 1.7
        return connection.creation.sql_remove_table_constraints(
            model, remove_refs, color.no_style())


def sql_add_constraints(connection, model, refs):
    """Return SQL statements for adding constraints.

    This provides compatibility with all supported versions of Django.

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

    Returns:
        list:
        The list of SQL statements for adding constraints.
    """
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        meta = model._meta

        if not meta.managed or meta.swapped:
            return []

        sql = []

        if model in refs:
            with connection.schema_editor() as schema_editor:
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
                        meta.get_field(get_remote_field(f).field_name)
                        .column
                    )

                    suffix = '_fk_%(to_table)s_%(to_column)s' % {
                        'to_table': meta.db_table,
                        'to_column': to_column,
                    }

                    name = schema_editor._create_index_name(rel_class,
                                                            [f.column],
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
    else:
        # Django < 1.7
        return connection.creation.sql_for_pending_references(
            model, color.no_style(), refs)


def create_index_name(connection, table_name, field_names=[], col_names=[],
                      unique=False, suffix=''):
    """Return the name for an index for a field.

    This provides compatibility with all supported versions of Django.

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
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        #
        # Fake a table for the call. It only needs _meta.db_table.
        class TempModel(object):
            class _meta:
                db_table = table_name

        if unique:
            assert not suffix
            suffix = '_uniq'

        with connection.schema_editor() as schema_editor:
            return schema_editor._create_index_name(TempModel,
                                                    col_names or field_names,
                                                    suffix=suffix)
    elif django.VERSION[:2] >= (1, 5):
        # Django >= 1.5, < 1.7
        #
        # This comes from sql_indexes_for_fields().
        index_name = '%s_%s' % (table_name,
                                digest(connection, field_names))

        return truncate_name(index_name, connection.ops.max_name_length())
    else:
        # Django < 1.5
        #
        # This whole block of logic comes from sql_indexes_for_field
        # in django.db.backends.creation, and is designed to match
        # the logic for the past few versions of Django.
        if supports_index_together:
            # Starting in Django 1.5, the _digest is passed a raw
            # list. While this is probably a bug (digest should
            # expect a string), we still need to retain
            # compatibility.
            #
            # It also uses the field name, and not the column name.
            column = field_names[0]
        else:
            column = col_names[0]

        column = digest(connection, column)

        return truncate_name('%s_%s' % (table_name, column),
                             connection.ops.max_name_length())


def create_index_together_name(connection, table_name, field_names):
    """Return the name of an index for an index_together.

    This provides compatibility with all supported versions of Django >= 1.5.
    Prior versions don't support index_together.

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
    if BaseDatabaseSchemaEditor:
        # Django >= 1.7
        #
        # Starting in 1.7, the index_together indexes were given a "_idx"
        # suffix.
        return create_index_name(connection, table_name, field_names,
                                 field_names, suffix='_idx')
    else:
        # Django < 1.7
        #
        # index_together was introduced in Django 1.5, and prior to 1.7, the
        # format was identical to that of normal indexes.
        assert django.VERSION[:2] >= (1, 5)

        index_name = '%s_%s' % (table_name, digest(connection, field_names))

        return truncate_name(index_name, connection.ops.max_name_length())


def create_constraint_name(connection, r_col, col, r_table, table):
    """Return the name of a constraint.

    This provides compatibility with all supported versions of Django.

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
    if BaseDatabaseSchemaEditor:
        suffix = '_fk_%(to_table)s_%(to_column)s' % {
            'to_table': table,
            'to_column': col,
        }

        # No need to truncate here, since create_index_name() will do it for
        # us.
        return create_index_name(connection, r_table, col_names=[r_col],
                                 suffix=suffix)
    else:
        return truncate_name(
            '%s_refs_%s_%s' % (r_col, col, digest(connection, r_table, table)),
            connection.ops.max_name_length())


def db_router_allows_syncdb(database, model_cls):
    """Return whether a database router allows syncdb operations for a model.

    This will only return ``True`` for Django 1.6 and older and if the
    router allows syncdb operations.

    Args:
        database (unicode):
            The name of the database.

        model_cls (type):
            The model class.

    Returns:
        bool:
        ``True`` if routers allow syncdb for this model.
    """
    return (django.VERSION[:2] <= (1, 6) and
            router.allow_syncdb(database, model_cls))


def db_router_allows_migrate(database, app_label, model_cls):
    """Return whether a database router allows migrate operations for a model.

    This will only return ``True`` for Django 1.7 and newer and if the
    router allows migrate operations. This is compatible with both the
    Django 1.7 and 1.8+ versions of ``allow_migrate``.

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
    if django.VERSION[:2] >= (1, 8):
        return router.allow_migrate_model(database, model_cls)
    elif django.VERSION[:2] == (1, 7):
        return router.allow_migrate(database, model_cls)
    else:
        return False


__all__ = [
    'atomic',
    'create_constraint_name',
    'create_index_name',
    'create_index_together_name',
    'digest',
    'db_router_allows_syncdb',
    'db_router_allows_migrate',
    'sql_add_constraints',
    'sql_delete_constraints',
    'sql_create',
    'sql_create_for_many_to_many_field',
    'sql_delete',
    'sql_indexes_for_field',
    'sql_indexes_for_fields',
    'sql_indexes_for_model',
    'truncate_name',
]
