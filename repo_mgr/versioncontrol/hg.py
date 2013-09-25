from ..api import *

from trac.perm import PermissionSystem
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

            options = ('deny_read', 'deny_push', 'allow_read', 'allow_push')
            if hgrc.has_section('web'):
                for option in options:
                    if hgrc.has_option('web', option):
                        hgrc.remove_option('web', option)
            else:
                hgrc.add_section('web')

            def apply_user_list(users, action):
                if not users:
                    hgrc.set('web', 'deny_' + action, '*')
                elif 'authenticated' in users:
                    if not 'anonymous' in users:
                        hgrc.set('web', 'deny_' + action, 'anonymous')
                else:
                    hgrc.set('web', 'allow_' + action, ', '.join(users))

            apply_user_list(reader, 'read')
            apply_user_list(writer, 'push')

            with open(hgrc_path, 'wb') as hgrc_file:
                hgrc.write(hgrc_file)

def expand_user_set(env, users):
    all_permissions = PermissionSystem(env).get_all_permissions()

    special_users = set(['anonymous', 'authenticated'])
    known_users = {u[0] for u in env.get_known_users()} | special_users
    valid_users = {perm[0] for perm in all_permissions} & known_users

    groups = set()
    user_list = list(users)
    for name in user_list:
        if name[0] == '@':
            groups |= set([name])
            for perm in (perm for perm in all_permissions
                         if perm[1] == name[1:]):
                if perm[0] in valid_users:
                    user_list.append(perm[0])
                elif not perm[0] in groups:
                    user_list.append('@' + perm[0])
    return set(user_list) - groups
