"""Compatibility functions for model-related operations.

This provides functions for working with models or importing moved fields.
These translate to the various versions of Django that are supported.
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings
from django.utils.module_loading import import_string


def get_default_auto_field():
    """Return the default auto field type.

    Version Added:
        3.0

    Returns:
        str:
        The dotted-notation name of the class to use for models that don't
        have a field with ``primary_key=True``.
    """
    return getattr(settings, 'DEFAULT_AUTO_FIELD',
                   'django.db.models.AutoField')


@lru_cache
def get_default_auto_field_cls():
    """Return the default auto field type.

    Version Added:
        3.0

    Returns:
        type:
        The field class to use for models that don't have a field with
        ``primary_key=True``.
    """
    return import_string(get_default_auto_field())
