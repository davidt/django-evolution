from optparse import make_option
import sys
import copy
try:
    import cPickle as pickle
except ImportError:
    import pickle as pickle
    
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db.models import get_apps, get_app, signals
from django.db import connection,transaction

from django_evolution import EvolutionException, CannotSimulate, SimulationFailure
from django_evolution.models import Evolution
from django_evolution.management.signature import create_project_sig
from django_evolution.management.diff import Diff
from django_evolution.evolve import get_mutations, simulate_mutations, compile_mutations

class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--verbosity', action='store', dest='verbosity', default='1',
            type='choice', choices=['0', '1', '2'],
            help='Verbosity level; 0=minimal output, 1=normal output, 2=all output'),
        make_option('--noinput', action='store_false', dest='interactive', default=True,
            help='Tells Django to NOT prompt the user for input of any kind.'),        
        make_option('--hint', action='store_true', dest='hint', default=False,
            help='Generate an evolution script that would update the app.'),
        make_option('--sql', action='store_true', dest='compile_sql', default=False,
            help='Compile a Django evolution script into SQL.'),
        make_option('-x','--execute', action='store_true', dest='execute', default=False,
            help='Apply the evolution to the database.'),
    )
    help = 'Evolve the models in a Django project.'
    args = '<appname appname ...>'

    requires_model_validation = False

    def handle(self, *app_labels, **options):
        verbosity = int(options['verbosity'])        
        interactive = options['interactive']
        execute = options['execute']
        compile_sql = options['compile_sql']
        hint = options['hint']
        
        # Use the list of all apps, unless app labels are specified.
        if app_labels:
            if execute:
                raise CommandError('Cannot specify an application name when executing evolutions.')
            try:
                app_list = [get_app(app_label) for app_label in app_labels]
            except (ImproperlyConfigured, ImportError), e:
                raise CommandError("%s. Are you sure your INSTALLED_APPS setting is correct?" % e)
        else:
            app_list = get_apps()

        # Iterate over all applications running the mutations
        evolution_required = False
        simulated = True
        evolution_required = False
        all_sql = []
        new_evolutions = []
        
        current_proj_sig = create_project_sig()
        signature = pickle.dumps(current_proj_sig)
        
        try:
            latest_evolution = Evolution.objects.latest('when')
            if latest_evolution.signature != signature:
                # Migration Required. Evolve the model.
                evolution_required = True
                if verbosity > 1:
                    print 'Project requires evolution'
                latest_evolution_sig = pickle.loads(str(latest_evolution.signature))
                
                if hint:
                    diff = Diff(latest_evolution_sig, current_proj_sig)
                    mutations = diff.evolution()
                else:
                    try:
                        mutations = get_mutations(app, latest_evolution.version)
                    except EvolutionException, e:
                        print self.style.ERROR(e)
                        sys.exit(1)
                                                  
                # Simulate the operation of the mutations
                try:
                    for app_label,app_sig in current_proj_sig.items():
                        if app_label in mutations:
                            simulate_mutations(app_label, mutations[app_label], latest_evolution_sig, current_proj_sig)
                except SimulationFailure, failure:
                    print self.style.ERROR('Simulated evolution of application %s did not succeed:' % failure.diff.app_label)
                    print failure.diff
                    sys.exit(1)
                except CannotSimulate:
                    simulated = False
                    
                # Compile the mutations into SQL
                
                for app_label in current_proj_sig:
                    if app_label in mutations:
                        all_sql.extend(compile_mutations(app_label, mutations[app_label], latest_evolution_sig))
                if execute:
                    # Create (BUT DONT SAVE) the new evolution table entry
                    if hint:
                        # Hinted evolutions are stored as temporary versions
                        version = None
                    else:
                        # If not hinted, we need to find and increment the version number
                        full_evolutions = Evolution.objects.filter(version__isnull=False)
                        last_full_evolution = full_evolutions[0]
                        version = last_full_evolution.version + 1 
                    new_evolution = Evolution(version=version,
                                              signature=signature)
                    new_evolutions.append(new_evolution)                     
                else:
                    
                    if compile_sql:
                        for app_label in current_proj_sig:
                            if app_label in mutations:
                                print ';; Compiled evolution SQL for %s' % app_label
                                for s in all_sql:
                                    print s                            
                    else:
                        for app_label in current_proj_sig:
                            if app_label in mutations:
                                print '----- Evolution for %s' % app_label
                                print 'from django_evolution.mutation import *'
                                print 'from django.db import models'
                                print 
                                print 'MUTATIONS = ['
                                print '   ',
                                print ',\n    '.join([str(m) for m in mutations[app_label]])
                                print ']'
                                print '----------------------'
            else:
                if verbosity > 1:
                    print 'Application %s is up to date' % app_name
        except Evolution.DoesNotExist:
            print self.style.ERROR("Can't evolve yet. Need to set an evolution baseline.")
            sys.exit(1)

        if evolution_required:
            if execute:
                # Now that we've worked out the mutations required, 
                # and we know they simulate OK, run the evolutions
                if interactive:
                    confirm = raw_input("""
You have requested a database evolution. This will alter tables 
and data currently in the %r database, and may result in 
IRREVERSABLE DATA LOSS. Evolutions should be *thoroughly* reviewed 
prior to execution. 

Are you sure you want to execute the evolutions?

Type 'yes' to continue, or 'no' to cancel: """ % settings.DATABASE_NAME)
                else:
                    confirm = 'yes'
                
                if confirm:
                    try:
                        # Begin Transaction
                        transaction.enter_transaction_management()
                        transaction.managed(True)
                        cursor = connection.cursor()
                        
                        # Perform the SQL
                        for statement in all_sql:
                            cursor.execute(statement)  
                        
                        # Now update the evolution table
                        for new_evolution in new_evolutions:
                            new_evolution.save()
                        
                        transaction.commit()
                        transaction.leave_transaction_management()
                    except Exception, ex:
                        transaction.rollback()
                        print self.style.ERROR('Error during evolution of %s: %s' % (app_name, str(ex)))
                        sys.exit(1)
                        
                    if verbosity > 0:
                        print 'Evolution successful.'
                else:
                    print 'Evolution cancelled.'
            elif not compile_sql:
                if verbosity > 0:
                    if simulated:
                        print "Trial evolution successful."
                        print "Run './manage.py evolve --execute' to apply evolution."
                    else:
                        print self.style.NOTICE('Evolution could not be simulated, possibly due to raw SQL mutations')
        else:
            if verbosity > 0:
                print 'No evolution required.'