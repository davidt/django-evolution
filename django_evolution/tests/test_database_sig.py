from __future__ import unicode_literals

from django.test.testcases import TestCase

from django_evolution.db import EvolutionOperationsMulti
from django_evolution.signature import create_database_sig


class DatabaseSigTests(TestCase):
    """Testing database signatures."""
    def setUp(self):
        self.database_sig = create_database_sig('default')
        self.evolver = EvolutionOperationsMulti('default').get_evolver()

    def test_initial_state(self):
        """Testing initial state of database_sig"""
        tables = self.database_sig.keys()

        # Check that a few known tables are in the list, to make sure
        # the scan worked.
        self.assertIn('django_content_type', tables)
        self.assertIn('django_evolution', tables)
        self.assertIn('django_project_version', tables)

        self.assertTrue('indexes' in self.database_sig['django_evolution'])

        # Check the Evolution model
        indexes = self.database_sig['django_evolution']['indexes']

        self.assertIn(
            {
                'unique': False,
                'columns': ['version_id'],
            },
            indexes.values())
