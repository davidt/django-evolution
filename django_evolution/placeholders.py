"""Placeholder objects for hinted evolutions.

Version Added:
    2.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.translation import gettext as _

from django_evolution.errors import EvolutionException

if TYPE_CHECKING:
    from typing import ClassVar


class BasePlaceholder:
    """A placeholder object for use in generating hints.

    Placeholder objects provide stand-ins for values that must be hand-added
    to the evolution file.

    Version Added:
        2.2
    """

    #: The text used in the placeholder.
    #:
    #: Type:
    #:     str
    placeholder_text: ClassVar[str | None] = None

    def __init__(
        self,
        app_label: (str | None) = None,
        model_name: (str | None) = None,
        field_name: (str | None) = None,
    ) -> None:
        """Initialize the object.

        Args:
            app_label (str, optional):
                The label of the application owning the model.

            model_name (str, optional):
                The name of the model owning the field.

            field_name (str, optional):
                The name of the field to return an initial value for.
        """
        self.app_label = app_label
        self.model_name = model_name
        self.field_name = field_name

    def __repr__(self) -> str:
        """Return a string representation of the object.

        This is used when outputting the value in a hinted evolution.

        Returns:
            str:
            The placeholder text.
        """
        return self.placeholder_text or ''

    def __call__(self) -> None:
        """Handle calls on this object.

        This will raise an exception stating that the evolution cannot be
        performed.

        Raises:
            django_evolution.errors.EvolutionException:
                An error stating that an explicit initial value must be
                provided in place of this object.
        """
        raise EvolutionException(
            _('Cannot use hinted evolution: Mutation requires a '
              'user-specified value.'))


class NullFieldInitialCallback(BasePlaceholder):
    """A placeholder for an initial value for a field.

    This is used in place of an initial value in mutations for fields that
    don't allow NULL values and don't have an explicit initial value set.
    It will show up in hinted evolutions as ``<<USER VALUE REQUIRED>>`` and
    will fail to evolve.
    """

    placeholder_text: ClassVar[str | None] = '<<USER VALUE REQUIRED>>'

    def __call__(self) -> None:
        """Handle calls on this object.

        This will raise an exception stating that the evolution cannot be
        performed.

        Raises:
            django_evolution.errors.EvolutionException:
                An error stating that an explicit initial value must be
                provided in place of this object.
        """
        raise EvolutionException(
            _('Cannot use hinted evolution: AddField or ChangeField mutation '
              'for "%s.%s" in "%s" requires user-specified initial value.')
            % (self.model_name, self.field_name, self.app_label))
