from ..api import *

from trac.util.translation import _
from trac.config import Option, PathOption

from ConfigParser import ConfigParser

import os
import random
import string
import shutil

from libsvn.repos import *
import pysvn

class SubversionConnector(Component):
    """Add support for creating and managing SVN repositories."""

    implements(IAdministrativeRepositoryConnector)

    svn_authz_file = PathOption('repository-manager', 'svn_authz_file',
                                'svn.authz',
                                doc="""The path where the svn auhz file for
                                       repository access control via e.g.
                                       Apache should be created for managed
                                       repositories. If not set, no file will
                                       be written.
                                       """)
    pre_commit_hook = Option('repository-manager', 'svn_pre_commit',
                             doc="""Path to an executable that should be run
                                    before a change is committed to any SVN
                                    repository managed via this plugin.
                                    """)
    post_commit_hook = Option('repository-manager', 'svn_post_commit',
                              doc="""Path to an executable that should be run
                                     after a change was committed to any SVN
                                     repository managed via this plugin.
                                     """)

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
            if self.pre_commit_hook:
                os.symlink(os.path.join(self.env.path, self.pre_commit_hook),
                           os.path.join(repo['dir'], 'hooks/pre-commit'))
            if self.post_commit_hook:
                os.symlink(os.path.join(self.env.path, self.post_commit_hook),
                           os.path.join(repo['dir'], 'hooks/post-commit'))
        except Exception, e:
            raise TracError(_("Failed to initialize repository: ") + str(e))

    def update_auth_files(self, repositories):
        if not self.svn_authz_file:
            return

        authz_path = os.path.join(self.env.path, self.svn_authz_file)

        authz = ConfigParser()

        groups = set()
        for repo in repositories:
            groups |= {name for name in repo.maintainers() if name[0] == '@'}
            groups |= {name for name in repo.writers() if name[0] == '@'}
            groups |= {name for name in repo.readers() if name[0] == '@'}

        authz.add_section('groups')
        for group in groups:
            members = expand_user_set(self.env, [group])
            authz.set('groups', group[1:], ', '.join(sorted(members)))

        for repo in repositories:
            section = repo.reponame + ':/'
            authz.add_section(section)
            rw = repo.maintainers() | repo.writers()
            r = repo.readers() - rw

            if 'authenticated' in rw:
                if 'anonymous' in r and not 'anonymous' in rw:
                    r = set(['anonymous'])
                else:
                    r = set()

            def apply_user_list(users, action):
                if not users:
                    return
                if 'authenticated' in users:
                    if not 'anonymous' in users:
                        authz.set(section, 'anonymous', '')
                    authz.set(section, '*', action)
                    return
                for user in sorted(users):
                    authz.set(section, user, action)

            apply_user_list(rw, 'rw')
            apply_user_list(r, 'r')

        with open(authz_path, 'wb') as authz_file:
            authz.write(authz_file)
