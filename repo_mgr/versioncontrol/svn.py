from ..api import *

from trac.util.translation import _

import os
import random
import string
import shutil

from libsvn.repos import *
import pysvn

class SubversionConnector(Component):
    implements(IAdministrativeRepositoryConnector)

    def get_supported_types(self):
        yield ('svn', 0)

    def can_fork(self, type):
        return False

    def create(self, repo):
        try:
            characters = string.ascii_lowercase + string.digits
            layout = ''.join(random.choice(characters) for x in range(20))
            layout = os.path.join('/tmp', layout)
            os.makedirs(os.path.join(layout, 'trunk'))
            os.makedirs(os.path.join(layout, 'branches'))
            os.makedirs(os.path.join(layout, 'tags'))
            config = { 'fs-type': 'fsfs' }
            svn_repos_create(repo['dir'], '', '', None, config)
            client = pysvn.Client()
            client.set_default_username(repo['owner'])
            client.import_(layout, 'file://' + repo['dir'],
                           'Initial repository layout')
            shutil.rmtree(layout)
        except Exception, e:
            raise TracError(_("Failed to initialize repository: ") + str(e))
