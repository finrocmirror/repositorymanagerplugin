from ..api import *

from trac.util.translation import _

from ConfigParser import ConfigParser

import hglib
import os

class MercurialConnector(Component):
    implements(IAdministrativeRepositoryConnector)

    def get_supported_types(self):
        yield ('hg', 0)

    def can_fork(self, type):
        return True

    def create(self, repo):
        try:
            hglib.init(repo['dir'])
        except Exception, e:
            raise TracError(_("Failed to initialize repository: ") + str(e))

    def fork(self, repo):
        try:
            hglib.clone(repo['origin_url'], repo['dir'],
                        updaterev='null', pull=True)
        except Exception, e:
            raise TracError(_("Failed to clone repository: ") + str(e))

    def update_auth_files(self, repositories):
        for repo in repositories:
            writer = set([repo.owner]) | repo.maintainer | repo.writer
            writer = expand_user_set(self.env, writer)
            reader = expand_user_set(self.env, writer | repo.reader)

            hgrc_path = os.path.join(repo.directory, '.hg/hgrc')

            hgrc = ConfigParser()
            hgrc.read(hgrc_path)

            if hgrc.has_section('web'):
                try:
                    hgrc.remove_option('web', 'deny_read')
                    hgrc.remove_option('web', 'deny_push')
                    hgrc.remove_option('web', 'allow_read')
                    hgrc.remove_option('web', 'allow_push')
                except:
                    pass
            else:
                hgrc.add_section('web')

            if not reader:
                hgrc.set('web', 'deny_read', '*')
            if not writer:
                hgrc.set('web', 'deny_push', '*')
            if not 'anonymous' in reader:
                hgrc.set('web', 'allow_read', ', '.join(reader))
            if not 'anonymous' in writer:
                hgrc.set('web', 'allow_push', ', '.join(writer))

            with open(hgrc_path, 'wb') as hgrc_file:
                hgrc.write(hgrc_file)
