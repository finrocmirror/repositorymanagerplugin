from ..api import *

from trac.util.translation import _

import os, random, string, shutil
from libsvn.repos import *
import pysvn

class SubversionConnector(Component):
    implements(IAdministrativeRepositoryConnector)

    def get_supported_types(self):
        yield ('svn', 0)

    def can_fork(self, repository_type):
        return False

    def create(self, repository):
        try:
            layout = ''.join(random.choice(string.ascii_lowercase + string.digits) for x in range(20))
            layout = os.path.join('/tmp', layout)
            os.makedirs(os.path.join(layout, 'trunk'))
            os.makedirs(os.path.join(layout, 'branches'))
            os.makedirs(os.path.join(layout, 'tags'))
            svn_repos_create(repository['directory'], '', '', None, { 'fs-type': 'fsfs' })
            client = pysvn.Client()
            client.set_default_username(repository['owner'])
            client.import_(layout, 'file://' + repository['directory'], 'Initial repository layout')
            shutil.rmtree(layout)
        except Exception, e:
            raise TracError(_("Failed to initialize repository: " + str(e)))
