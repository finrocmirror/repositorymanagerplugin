from ..api import *

from trac.util.translation import _

from ConfigParser import ConfigParser

import hglib
import os

class MercurialConnector(Component):
    """Add support for creating and managing HG repositories."""

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
            writers = repo.maintainers() | repo.writers()
            writers = expand_user_set(self.env, writers)
            readers = expand_user_set(self.env, writers | repo.readers())

            hgrc_path = os.path.join(repo.directory, '.hg/hgrc')

            hgrc = ConfigParser()
            hgrc.read(hgrc_path)

            options = ('deny_read', 'deny_push',
                       'allow_read', 'allow_push', 'allow_write')
            if hgrc.has_section('web'):
                for option in options:
                    if hgrc.has_option('web', option):
                        hgrc.remove_option('web', option)
            else:
                hgrc.add_section('web')

            if repo.description:
                hgrc.set('web', 'description', repo.description)

            def apply_user_list(users, action):
                if not users:
                    hgrc.set('web', 'deny_' + action, '*')
                    return
                if 'anonymous' in users:
                    return
                if 'authenticated' in users:
                    hgrc.set('web', 'deny_' + action, 'anonymous')
                    return
                hgrc.set('web', 'allow_' + action, ', '.join(sorted(users)))

            apply_user_list(readers, 'read')
            if repo.maintainers():
                apply_user_list(writers, 'write')
            else:
                apply_user_list(writers, 'push')

            with open(hgrc_path, 'wb') as hgrc_file:
                hgrc.write(hgrc_file)
            try:
                modes = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
                os.chmod(hgrc_path, modes)
            except:
                pass
