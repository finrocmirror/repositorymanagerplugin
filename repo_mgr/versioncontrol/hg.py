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

def expand_user_set(env, users):
    all_permissions = PermissionSystem(env).get_all_permissions()

    known_users = {u[0] for u in env.get_known_users()} | set(['anonymous'])
    valid_users = {perm[0] for perm in all_permissions} & known_users

    groups = set()
    user_list = list(users)
    for user in user_list:
        if user[0] == '@':
            groups |= set([user])
            for perm in (perm for perm in all_permissions
                         if perm[1] == user[1:]):
                if perm[0] in valid_users:
                    user_list.append(perm[0])
                elif not perm[0] in groups:
                    user_list.append('@' + perm[0])
    return set(user_list) - groups
