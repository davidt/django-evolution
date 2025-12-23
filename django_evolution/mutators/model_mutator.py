"""Mutator that applies changes to a model.

Version Added:
    2.2
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django_evolution.db import EvolutionOperationsMulti
from django_evolution.errors import EvolutionBaselineMissingError
from django_evolution.mock_models import MockModel
from django_evolution.mutations import BaseModelMutation
from django_evolution.mutators.base import BaseAppStateMutator
from django_evolution.utils.models import get_database_for_model_name

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from django.db import models

    from django_evolution.mutations import (
        ChangeField,
        ChangeMeta,
        DeleteField,
        DeleteModel,
        SQLMutation,
    )
    from django_evolution.mutators.app_mutator import AppMutator
    from django_evolution.signature import ModelSignature


logger = logging.getLogger(__name__)


class ModelMutator(BaseAppStateMutator):
    """Tracks and runs mutations for a model.

    A ModelMutator is bound to a particular model (by type, not instance) and
    handles operations that apply to that model.

    Operations are first registered by mutations, and then later provided to
    the database's operations backend, where they will be applied to the
    database.

    After all operations are added, the caller is expected to call to_sql()
    to get the SQL statements needed to apply those operations. Once called,
    the mutator is finalized, and new operations cannot be added.

    ModelMutator only works with mutations that are instances of
    BaseModelFieldMutation.

    This is instantiated by :py:class:`~django_evolution.mutators.app_mutator.
    AppMutator`, and should not be created manually.

    Version Changed:
        2.2:
        Moved into the :py:mod:`django_evolution.mutators.model_mutator`
        module.
    """

    def __init__(
        self,
        app_mutator: AppMutator,
        model_name: str,
    ) -> None:
        """Initialize the mutator.

        Args:
            app_mutator (django_evolution.mutators.app_mutator.AppMutator):
                The app mutator that owns this model mutator.

            model_name (str):
                The name of the model being evolved.
        """
        super().__init__(app_mutator=app_mutator)

        if not self.database:
            self.database = get_database_for_model_name(self.app_label,
                                                        model_name)
            assert self.database

        self.model_name = model_name
        self._ops = []

        evolution_ops = EvolutionOperationsMulti(self.database,
                                                 self.database_state)
        self.evolver = evolution_ops.get_evolver()

    @property
    def model_sig(self) -> ModelSignature:
        """The model signature that this mutator is working with.

        Type:
            django_evolution.signature.ModelSignature

        Raises:
            django_evolution.errors.EvolutionBaselineMissingError:
                The model signature or parent app signature could not be found.
        """
        app_label = self.app_label
        app_sig = self.project_sig.get_app_sig(app_label)

        if app_sig is None:
            if (self.legacy_app_label is not None and
                self.legacy_app_label != app_label):
                # Check if it can be found by the legacy label.
                app_sig = self.project_sig.get_app_sig(self.legacy_app_label)

            if app_sig is None:
                raise EvolutionBaselineMissingError(
                    'The app signature for "%s" could not be found.'
                    % app_label)

        model_sig = app_sig.get_model_sig(self.model_name)

        if model_sig is None:
            raise EvolutionBaselineMissingError(
                'The model signature for "%s.%s" could not be found.'
                % (app_label, self.model_name))

        return model_sig

    def create_model(self) -> MockModel:
        """Create a mock model instance with the stored information.

        This is typically used when calling a mutation's mutate() function
        and passing a model instance, but can also be called whenever
        a new instance of the model is needed for any lookups.

        Returns:
            django_evolution.mock_models.MockModel:
            The resulting mock model.

        Raises:
            django_evolution.errors.EvolutionBaselineMissingError:
                The model signature or parent app signature could not be found.
        """
        return MockModel(project_sig=self.project_sig,
                         app_name=self.app_label,
                         model_name=self.model_name,
                         model_sig=self.model_sig,
                         db_name=self.database)

    def add_column(self, mutation, field, initial):
        """Adds a pending Add Column operation.

        This will cause to_sql() to include SQL for adding the column
        with the given information to the model.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'add_column',
            'mutation': mutation,
            'field': field,
            'initial': initial,
        })

    def change_column_type(
        self,
        mutation: ChangeField,
        old_field: models.Field,
        new_field: models.Field,
        new_attrs: Mapping[str, Any],
    ) -> None:
        """Add a pending Change Column Type operation.

        This will cause :py:meth:`to_sql` to include SQL for changing a field
        to a new type.

        Args:
            mutation (django_evolution.mutations.ChangeField):
                The mutation that triggered this column type change.

            old_field (django.db.models.Field):
                The old field on the model.

            new_field (django.db.models.Field):
                The new field on the model.

            new_attrs (dict):
                New attributes set in the
                :py:class:`~django_evolution.mutations.change_field.
                ChangeField`.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'change_column_type',
            'mutation': mutation,
            'old_field': old_field,
            'new_field': new_field,
            'new_attrs': new_attrs,
        })

    def change_column(
        self,
        mutation: ChangeField,
        field: models.Field,
        new_attrs: Mapping[str, Any],
    ) -> None:
        """Add a pending Change Column operation.

        This will cause to_sql() to include SQL for changing one or more
        attributes for the given column.

        Args:
            mutation (django_evolution.mutations.ChangeField):
                The mutation that triggered this column type change.

            field (django.db.models.Field):
                The field on the model.

            new_attrs (dict):
                New attributes set in the
                :py:class:`~django_evolution.mutations.change_field.
                ChangeField`.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'change_column',
            'mutation': mutation,
            'field': field,
            'new_attrs': new_attrs,
        })

    def delete_column(
        self,
        mutation: DeleteField,
        field: models.Field,
    ) -> None:
        """Add a pending Delete Column operation.

        This will cause to_sql() to include SQL for deleting the given
        column.

        Args:
            mutation (django_evolution.mutations.DeleteField):
                The mutation that triggered this column deletion.

            field (django.db.models.Field):
                The field on the model.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'delete_column',
            'mutation': mutation,
            'field': field,
        })

    def delete_model(
        self,
        mutation: DeleteModel,
    ) -> None:
        """Add a pending Delete Model operation.

        This will cause to_sql() to include SQL for deleting the model.

        Args:
            mutation (django_evolution.mutations.DeleteModel):
                The mutation that triggered this model deletion.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'delete_model',
            'mutation': mutation,
        })

    def change_meta(
        self,
        mutation: ChangeMeta,
        prop_name: str,
        new_value: Any,
    ) -> None:
        """Add a pending Change Meta operation.

        This will cause to_sql() to include SQL for changing a supported
        attribute in the model's Meta class.

        Args:
            mutation (django_evolution.mutations.ChangeMeta):
                The mutation that triggered this metadata change.

            prop_name (str):
                The name of the property being changed.

            new_value (object):
                The new value being assigned.
        """
        assert not self.finalized

        if prop_name in {'index_together', 'unique_together'}:
            old_value = getattr(self.model_sig, prop_name)
        elif prop_name == 'constraints':
            # Django >= 2.2
            old_value = [
                dict({
                    'name': constraint_sig.name,
                    'type': constraint_sig.type,
                }, **constraint_sig.attrs)
                for constraint_sig in self.model_sig.constraint_sigs
            ]
        elif prop_name == 'db_table_comment':
            # Django >= 4.2
            old_value = self.model_sig.db_table_comment
        elif prop_name == 'indexes':
            # Django >= 1.11
            old_value = []

            for index_sig in self.model_sig.index_sigs:
                index_value = index_sig.attrs.copy()

                if index_sig.expressions:
                    index_value['expressions'] = index_sig.expressions

                if index_sig.fields:
                    index_value['fields'] = index_sig.fields

                if index_sig.name:
                    index_value['name'] = index_sig.name

                old_value.append(index_value)
        else:
            raise ValueError('Cannot change meta property "%s"' % prop_name)

        self._ops.append({
            'type': 'change_meta',
            'mutation': mutation,
            'prop_name': prop_name,
            'old_value': old_value,
            'new_value': new_value,
        })

    def add_sql(
        self,
        mutation: SQLMutation,
        sql: str,
    ) -> None:
        """Add an operation for executing custom SQL.

        This will cause to_sql() to include the provided SQL statements.
        The SQL should be a list of a statements.

        Args:
            mutation (django_evolution.mutations.SQLMutation):
                The mutation that triggered change.

            sql (str):
                The SQL to execute.
        """
        assert not self.finalized

        self._ops.append({
            'type': 'sql',
            'mutation': mutation,
            'sql': sql,
        })

    def run_mutation(
        self,
        mutation: BaseModelMutation,
    ) -> None:
        """Run the specified mutation.

        The mutation will be provided with a temporary mock instance of a
        model that can be used for field or meta lookups.

        The mutator must be finalized before this can be called.

        Once the mutation has been run, it will call :py:meth:`run_simulation`,
        applying changes to the database project signature.

        Args:
            mutation (django_evolution.mutations.BaseModelMutation):
                The mutation to run.

        Raises:
            django_evolution.errors.EvolutionBaselineMissingError:
                The model signature or parent app signature could not be found.
        """
        assert isinstance(mutation, BaseModelMutation)

        logger.debug('Running mutation for %s.%s: %r',
                     self.app_label, self.model_name, mutation)

        super().run_mutation(
            mutation=mutation,
            mutate_kwargs={
                'model': self.create_model(),
            })

    def to_sql(self):
        """Returns SQL for the operations added to this mutator.

        The SQL will represent all the operations made by the mutator,
        as determined by the database operations backend.

        Once called, no new operations can be added to the mutator.
        """
        assert not self.finalized

        self.finalize()

        return self.evolver.generate_table_ops_sql(self, self._ops)

    def finish_op(
        self,
        op,
    ) -> None:
        """Finish handling an operation.

        This is called by the evolution operations backend when it is done
        with an operation.

        Simulations for the operation's associated mutation will be applied,
        in order to update the signatures for the changes made by the
        mutation.

        Args:
            op (dict):
                The operation that has finished.
        """
        self.run_simulation(op['mutation'])
