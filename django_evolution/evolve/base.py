"""Base classes for evolver-related objects.

Version Added:
    2.2:
    This was previously located in :py:mod:`django_evolution.evolve`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.db.backends.utils import CursorWrapper

    from django_evolution.evolve.evolver import Evolver
    from django_evolution.mutations.base import BaseMutation
    from django_evolution.utils.sql import SQLExecutor


class BaseEvolutionTask:
    """Base class for a task to perform during evolution.

    Attributes:
        can_simulate (bool):
            Whether the task can be simulated without requiring additional
            information.

            This is set after calling :py:meth:`prepare`.

        evolution_required (bool):
            Whether an evolution is required by this task.

            This is set after calling :py:meth:`prepare`.

        evolver (django_evolution.evolve.evolver.Evolver):
            The evolver that will execute the task.

        id (str):
            The unique ID for the task.

        new_evolutions (list of django_evolution.models.Evolution):
            A list of evolution model entries this task would create.

            This is set after calling :py:meth:`prepare`.

        sql (list):
            A list of SQL statements to perform for the task. Each entry can
            be a string or tuple accepted by
            :py:meth:`~django_evolution.utils.sql.SQLExecutor.run_sql`.
    """

    @classmethod
    def prepare_tasks(
        cls,
        evolver: Evolver,
        tasks: Sequence[BaseEvolutionTask],
        **kwargs,
    ) -> None:
        """Prepare a list of tasks.

        This is responsible for calling :py:meth:`prepare` on each of the
        provided tasks. It can augment this by calculating any other state
        needed in order to influence the tasks or react to them.

        If this applies state to the class, it should always be careful to
        completely reset the state on each run, in case there are multiple
        :py:class:`Evolver` instances at work within a process.

        Args:
            evolver (django_evolution.evolve.evolver.Evolver):
                The evolver that's handling the tasks.

            tasks (list of BaseEvolutionTask):
                The list of tasks to prepare. These will match the current
                class.

            **kwargs (dict):
                Keyword arguments to pass to the tasks' `:py:meth:`prepare`
                methods.
        """
        for task in tasks:
            task.prepare(**kwargs)

    @classmethod
    def execute_tasks(
        cls,
        evolver: Evolver,
        tasks: Sequence[BaseEvolutionTask],
        **kwargs,
    ) -> None:
        """Execute a list of tasks.

        This is responsible for calling :py:meth:`execute` on each of the
        provided tasks. It can augment this by executing any steps before or
        after the tasks.

        If this applies state to the class, it should always be careful to
        completely reset the state on each run, in case there are multiple
        :py:class:`Evolver` instances at work within a process.

        This may depend on state from :py:meth:`prepare_tasks`.

        Args:
            evolver (django_evolution.evolve.evolver.Evolver):
                The evolver that's handling the tasks.

            tasks (list of BaseEvolutionTask):
                The list of tasks to execute. These will match the current
                class.

            **kwargs (dict):
                Keyword arguments to pass to the tasks' `:py:meth:`execute`
                methods.
        """
        with evolver.sql_executor(check_constraints=False) as sql_executor:
            for task in tasks:
                task.execute(sql_executor=sql_executor, **kwargs)

    def __init__(
        self,
        task_id: str,
        evolver: Evolver,
    ) -> None:
        """Initialize the task.

        Args:
            task_id (str):
                The unique ID for the task.

            evolver (django_evolution.evolve.evolver.Evolver):
                The evolver that will execute the task.
        """
        self.id = task_id
        self.evolver = evolver

        self.can_simulate = False
        self.evolution_required = False
        self.new_evolutions = []
        self.sql = []

    def is_mutation_mutable(
        self,
        mutation: BaseMutation,
        **kwargs,
    ) -> bool:
        """Return whether a mutation is mutable.

        This is a handy wrapper around :py:meth:`BaseMutation.is_mutable
        <django_evolution.mutations.BaseMutation.is_mutable>` that passes
        standard arguments based on evolver state. Callers should pass any
        additional arguments that are required as keyword arguments.

        Args:
            mutation (django_evolution.mutations.BaseMutation):
                The mutation to check.

            **kwargs (dict):
                Additional keyword arguments to pass to
                :py:meth:`BaseMutation.is_mutable
                <django_evolution.mutations.BaseMutation.is_mutable>`.

        Returns:
            bool:
            ``True`` if the mutation is mutable. ``False`` if it is not.
        """
        evolver = self.evolver

        return mutation.is_mutable(project_sig=evolver.project_sig,
                                   database_state=evolver.database_state,
                                   database=evolver.database_name,
                                   **kwargs)

    def prepare(
        self,
        hinted: bool,
        **kwargs,
    ) -> None:
        """Prepare state for this task.

        This is responsible for determining whether the task applies to the
        database. It must set :py:attr:`evolution_required`,
        :py:attr:`new_evolutions`, and :py:attr:`sql`.

        This must be called before :py:meth:`execute` or
        :py:meth:`get_evolution_content`.

        Args:
            hinted (bool):
                Whether to prepare the task for hinted evolutions.

            **kwargs (dict, unused):
                Additional keyword arguments passed for task preparation.
                This is provide for future expansion purposes.
        """
        raise NotImplementedError

    def execute(
        self,
        cursor: (CursorWrapper | None) = None,
        sql_executor: (SQLExecutor | None) = None,
        **kwargs,
    ) -> None:
        """Execute the task.

        This will make any changes necessary to the database.

        Version Changed:
            2.1:
            ``cursor`` is now deprecated in favor of ``sql_executor``.

        Args:
            cursor (django.db.backends.utils.CursorWrapper, optional):
                The legacy database cursor used to execute queries.

            sql_executor (django_evolution.utils.sql.SQLExecutor, optional):
                The SQL executor used to run any SQL on the database.

            **kwargs (dict):
                Additional keyword arguments, for future expansion.

        Raises:
            django_evolution.errors.EvolutionExecutionError:
                The evolution task failed. Details are in the error.
        """
        raise NotImplementedError

    def get_evolution_content(self) -> str:
        """Return the content for an evolution file for this task.

        Returns:
            str:
            The evolution content.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """Return a string representation of the task.

        Returns:
            str:
            The string representation.
        """
        return '<%s(id=%s)>' % (type(self).__name__, self.id)

    def __str__(self) -> str:
        """Return a string description of the task.

        Returns:
            str:
            The string description.
        """
        raise NotImplementedError
