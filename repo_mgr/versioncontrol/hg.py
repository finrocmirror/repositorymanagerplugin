from ..api import *

from trac.util.translation import _

import hglib

class MercurialConnector(Component):
    implements(IAdministrativeRepositoryConnector)

    def get_supported_types(self):
        yield ('hg', 0)

    def can_fork(self, repository_type):
        return True

    def create(self, repository):
        try:
            hglib.init(repository['directory'])
        except Exception, e:
            raise TracError(_('Failed to initialize repository: ' + str(e)))

    def fork(self, repository):
        try:
            hglib.clone(repository['origin_url'], repository['directory'], updaterev='null', pull=True)
        except Exception, e:
            raise TracError(_('Failed to clone repository: ' + str(e)))
