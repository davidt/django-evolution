"""Utilities for working with models."""

from __future__ import annotations

from collections import defaultdict

from django.apps.registry import apps
from django.db import router


_rel_tree_cache = None


def get_models(app_mod=None, include_auto_created=False):
    """Return the models belonging to an app.

    Version Changed:
        3.0:
        Moved from :py:mod:`django_evolution.compat.models`.

    Args:
        app_mod (module, optional):
            The application module.

        include_auto_created (bool, optional):
            Whether to return auto-created models (such as many-to-many
            models) in the results.

    Returns:
        list:
        The list of modules belonging to the app.
    """
    if app_mod is None:
        return apps.get_models(include_auto_created=include_auto_created)

    for app_config in apps.get_app_configs():
        if app_config.models_module is app_mod:
            return [
                model
                for model in app_config.get_models(
                    include_auto_created=include_auto_created)
                if not model._meta.abstract
            ]

    return []


def set_model_name(model, name):
    """Set the name of a model.

    Version Changed:
        3.0:
        Moved from :py:mod:`django_evolution.compat.models`.

    Args:
        model (django.db.models.Model):
            The model to set the new name on.

        name (str):
            The new model name.
    """
    model._meta.model_name = name


def get_model_name(model):
    """Return the model's name.

    Version Changed:
        3.0:
        Moved from :py:mod:`django_evolution.compat.models`.

    Args:
        model (django.db.models.Model):
            The model for which to return the name.

    Returns:
        str: The model's name.
    """
    return model._meta.model_name


def get_database_for_model_name(app_name, model_name):
    """Return the database used for a given model.

    Given an app name and a model name, this will return the proper
    database connection name used for making changes to that model. It
    will go through any custom routers that understand that type of model.

    Args:
        app_name (str):
            The name of the app owning the model.

        model_name (str):
            The name of the model.

    Returns:
        str:
        The name of the database used for the model.
    """
    return router.db_for_write(apps.get_model(app_name, model_name))


def walk_model_tree(model):
    """Walk through a tree of models.

    This will yield the provided model and its parents, in turn yielding
    their parents, and so on.

    Version Added:
        2.2

    Args:
        model (type):
            The top of the model tree to iterate through.

    Yields:
        type:
        Each model class in the tree.
    """
    yield model

    for parent in model._meta.parents:
        yield from walk_model_tree(parent)


def get_model_rel_tree():
    """Return the full field relationship tree for all registered models.

    This will walk through every field in every model registered in Django,
    storing the relationships between objects, caching them. Each entry in
    the resulting dictionary will be a table mapping to a list of relation
    fields that point back at it.

    This can be used to quickly locate any and all reverse relations made to
    a field.

    This is similar to Django's built-in reverse relation tree used internally
    (with different implementations) in
    :py:class:`django.db.models.options.Options`, but works across all
    supported versions of Django, and supports cache clearing.

    Version Added:
        2.2

    Returns:
        dict:
        The model relation tree.
    """
    global _rel_tree_cache

    if _rel_tree_cache is not None:
        return _rel_tree_cache

    rel_tree = defaultdict(list)
    all_models = get_models(include_auto_created=True)

    # We'll walk the entire model tree, looking for any immediate fields on
    # each model, building a mapping of models to fields that reference the
    # model.
    for cur_model in all_models:
        if cur_model._meta.abstract:
            continue

        for field in iter_model_fields(cur_model,
                                       include_parent_models=False,
                                       include_forward_fields=True,
                                       include_reverse_fields=False,
                                       include_hidden_fields=False):
            if (field.is_relation and
                field.related_model is not None):
                remote_field = field.remote_field
                remote_field_model = remote_field.model

                # Make sure this isn't a "self" relation or similar.
                if not isinstance(remote_field_model, str):
                    db_table = \
                        remote_field_model._meta.concrete_model._meta.db_table
                    rel_tree[db_table].append(field)

    _rel_tree_cache = rel_tree

    return rel_tree


def clear_model_rel_tree():
    """Clear the model relationship tree.

    This will cause the next call to :py:func:`get_model_rel_tree` to
    re-compute the full tree.

    Version Added:
        2.2
    """
    global _rel_tree_cache

    _rel_tree_cache = None


def iter_model_fields(model,
                      include_parent_models=True,
                      include_forward_fields=True,
                      include_reverse_fields=False,
                      include_hidden_fields=False,
                      seen_models=None):
    """Iterate through all fields on a model using the given criteria.

    This is roughly equivalent to Django's internal
    :py:func:`django.db.models.options.Option._get_fields` on Django 1.8+,
    but makes use of our model reverse relation tree, and works across all
    supported versions of Django.

    Version Added:
        2.2

    Args:
        model (type):
            The model owning the fields.

        include_parent_models (bool, optional):
            Whether to include fields defined on parent models.

        include_forward_fields (bool, optional):
            Whether to include fields owned by the model (or a parent).

        include_reverse_fields (bool, optional):
            Whether to include fields on other models that point to this
            model.

        include_hidden_fields (bool, optional):
            Whether to include hidden fields.

        seen_models (set, optional):
            Models seen during iteration. This is intended for internal
            use only by this function.

    Yields:
        django.db.models.Field:
        Each field matching the criteria.
    """
    concrete_model = model._meta.concrete_model

    if seen_models is None:
        seen_models = set()

    if include_parent_models:
        candidate_models = walk_model_tree(model)
    else:
        candidate_models = [model]

    if include_reverse_fields:
        # Find all models containing fields that point to this model.
        rel_tree = get_model_rel_tree()
        rel_fields = rel_tree.get(model._meta.concrete_model._meta.db_table,
                                  [])
    else:
        rel_fields = []

    for cur_model in candidate_models:
        cur_model_label = cur_model._meta.db_table

        if (cur_model_label in seen_models or
            cur_model._meta.concrete_model != concrete_model):
            continue

        seen_models.add(cur_model_label)

        if include_parent_models:
            for parent in cur_model._meta.parents:
                if parent not in seen_models:
                    parent_fields = iter_model_fields(
                        parent,
                        include_parent_models=True,
                        include_forward_fields=include_forward_fields,
                        include_reverse_fields=include_reverse_fields,
                        include_hidden_fields=include_hidden_fields)

                    for field in parent_fields:
                        yield field

        if include_reverse_fields and not cur_model._meta.proxy:
            for rel_field in rel_fields:
                remote_field = rel_field.remote_field

                if (include_hidden_fields or
                    not remote_field.hidden):
                    yield remote_field

        if include_forward_fields:
            for field in cur_model._meta.local_fields:
                yield field

            for field in cur_model._meta.local_many_to_many:
                yield field

    # Django >= 1.10
    for field in getattr(model._meta, 'private_fields', []):
        yield field


def iter_non_m2m_reverse_relations(field):
    """Iterate through non-M2M reverse relations pointing to a field.

    This will exclude any :py:class:`~django.db.models.ManyToManyField`s,
    but will include the relation fields on their "through" tables.

    Note that this may return duplicate results, or multiple relations
    pointing to the same field. It's up to the caller to handle this.

    Version Added:
        2.2

    Args:
        field (django.db.models.Field):
            The field that relations must point to.

    Yields:
        django.db.models.Field or object:
        Each field or relation object pointing to this field.

        The type of the relation object depends on the version of Django.
    """
    is_primary_key = field.primary_key
    field_name = field.name

    for rel in iter_model_fields(field.model,
                                 include_parent_models=True,
                                 include_forward_fields=False,
                                 include_reverse_fields=True,
                                 include_hidden_fields=True):
        rel_from_field = rel.field

        # Exclude any ManyToManyFields, and make sure the referencing fields
        # point directly to the ID on this field.
        if (not rel_from_field.many_to_many and
            ((is_primary_key and rel_from_field.to_fields == [None]) or
             field_name in rel_from_field.to_fields)):
            yield rel

            # Now do the same for the fields on the model of the related field.
            other_rel_fields = iter_non_m2m_reverse_relations(rel.remote_field)

            yield from other_rel_fields
