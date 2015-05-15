from trac.core import *
from trac.versioncontrol.api import RepositoryManager as TracRepositoryManager
from trac.versioncontrol.svn_authz import AuthzSourcePolicy
from trac.perm import PermissionSystem
from trac.util import as_bool
from trac.util.translation import _
from trac.config import Option, BoolOption

from ConfigParser import ConfigParser

import os
import errno
import stat
import shutil

class IAdministrativeRepositoryConnector(Interface):
    """Provide support for a specific version control system.

    Instead of Trac's usual IRepositoyConnector interface for navigating
    repositories of specific version control systems, this one is used
    for more administrative tasks like creation, forking, renaming or
    deleting repositories.
    """

    error = None

    def get_supported_types():
        """Return the supported types of version control systems.

        Yields `(repository type, priority)` pairs.

        If multiple provider match a given type, the `priority` is used
        to choose between them (highest number is highest priority).

        If the `priority` returned is negative, this indicates that the
        connector for the given `repository type` indeed exists but can
        not be used for some reason. The `error` property can then be
        used to store an error message or exception relevant to the
        problem detected.
        """

    def can_fork(repository_type):
        """Return whether forking is supported by the connector."""

    def can_delete_changesets(repository_type):
        """Return whether deleting changesets is supported."""

    def can_ban_changesets(repository_type):
        """Return whether banning changesets is supported."""

    def create(repository):
        """Create a new empty repository with given attributes."""

    def fork(repository):
        """Fork from `origin_url` in the given dict."""

    def delete_changeset(repository, revision, ban):
        """Delete (and optionally ban) a changeset from the repository."""

    def update_auth_files(repositories):
        """Write auth information to e.g. authz for .hgrc files"""

class RepositoryManager(Component):
    """Adds creation, modification and deletion of repositories.

    This class extends Trac's `RepositoryManager` and adds some
    capabilities that allow users to create and manage repositories.
    The original `RepositoryManager` *just* allows adding and removing
    existing repositories from Trac's database, which means that still
    someone must do some shell work on the server.

    To work nicely together with manually created and added repositories
    a new `ManagedRepository` class is used to mark the ones that can be
    handled by this module. It also implements forking, if the connector
    supports that, which creates instances of `ForkedRepository`.
    """

    base_dir = Option('repository-manager', 'base_dir', 'repositories',
                      doc="""The base folder in which repositories will be
                             created.
                             """)
    owner_as_maintainer = BoolOption('repository-manager',
                                     'owner_as_maintainer',
                                     True,
                                     doc="""If true, the owner will have the
                                            role of a maintainer, too.
                                            Otherwise, he will only act as an
                                            administrator for his repositories.
                                            """)

    connectors = ExtensionPoint(IAdministrativeRepositoryConnector)

    manager = None

    roles = ('maintainer', 'writer', 'reader')

    def __init__(self):
        self.manager = TracRepositoryManager(self.env)

    def get_supported_types(self):
        """Return the list of supported repository types."""
        types = set(type for connector in self.connectors
                    for (type, prio) in connector.get_supported_types() or []
                    if prio >= 0)
        return list(types & set(self.manager.get_supported_types()))

    def get_forkable_types(self):
        """Return the list of forkable repository types."""
        return list(type for type in self.get_supported_types()
                    if self.can_fork(type))

    def can_fork(self, type):
        """Return whether the given repository type can be forked."""
        return self._get_repository_connector(type).can_fork(type)

    def can_delete_changesets(self, type):
        """Return whether the given repository type can delete changesets."""
        return self._get_repository_connector(type).can_delete_changesets(type)

    def can_ban_changesets(self, type):
        """Return whether the given repository type can ban changesets."""
        return self._get_repository_connector(type).can_ban_changesets(type)

    def get_forkable_repositories(self):
        """Return a dictionary of repository information, indexed by
        name and including only repositories that can be forked."""
        repositories = self.manager.get_all_repositories()
        result = {}
        for key in repositories:
            if repositories[key]['type'] in self.get_forkable_types():
                result[key] = repositories[key]['name']
        return result

    def get_managed_repositories(self):
        """Return the list of existing managed repositories."""
        repositories = self.manager.get_all_repositories()
        result = {}
        for key in repositories:
            try:
                self.get_repository(repositories[key]['name'], True)
                result[key] = repositories[key]['name']
            except:
                pass
        return result

    def get_repository(self, name, convert_to_managed=False):
        """Retrieve the appropriate repository for the given name.

        Converts the found repository into a `ManagedRepository`, if
        requested. In that case, expect an exception if the found
        repository was not created using this `RepositoryManager`.
        """
        repo = self.manager.get_repository(name)
        if repo and convert_to_managed:
            convert_managed_repository(self.env, repo)
        return repo

    def get_repository_by_id(self, id, convert_to_managed=False):
        """Retrieve a matching `Repository` for the given id."""
        repositories = self.manager.get_all_repositories()
        for name, info in repositories.iteritems():
            if info['id'] == int(id):
                return self.get_repository(name, convert_to_managed)
        return None

    def get_repository_by_path(self, path):
        """Retrieve a matching `Repository` for the given path."""
        return self.manager.get_repository_by_path(path)

    def get_base_directory(self, type):
        """Get the base directory for the given repository type."""
        return os.path.join(self.env.path, self.base_dir, type)

    def create(self, repo):
        """Create a new empty repository.

         * Checks if the new repository can be created and added
         * Prepares the filesystem
         * Uses an appropriate connector to create and initialize the
           repository
         * Postprocesses the filesystem (modes)
         * Inserts everything into the database and synchronizes Trac
        """
        if self.get_repository(repo['name']) or os.path.lexists(repo['dir']):
            raise TracError(_("Repository or directory already exists."))

        self._prepare_base_directory(repo['dir'])

        self._get_repository_connector(repo['type']).create(repo)

        self._adjust_modes(repo['dir'])

        with self.env.db_transaction as db:
            id = self.manager.get_repository_id(repo['name'])
            roles = list((id, role + 's', '') for role in self.roles)
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', repo['dir']),
                 (id, 'type', repo['type']),
                 (id, 'owner', repo['owner'])] + roles)
            self.manager.reload_repositories()
        self.manager.get_repository(repo['name']).sync(None, True)
        self.update_auth_files()

    def fork_local(self, repo):
        """Fork a local repository.

         * Checks if the new repository can be created and added
         * Checks if the origin exists and can be forked
         * The filesystem is obviously already prepared
         * Uses an appropriate connector to fork the repository
         * Postprocesses the filesystem (modes)
         * Inserts everything into the database and synchronizes Trac
        """
        if self.get_repository(repo['name']) or os.path.lexists(repo['dir']):
            raise TracError(_("Repository or directory already exists."))

        origin = self.get_repository(repo['origin'], True)
        if not origin:
            raise TracError(_("Origin for local fork does not exist."))
        if origin.type != repo['type']:
            raise TracError(_("Fork of local repository must have same type "
                              "as origin."))
        repo.update({'origin_url': 'file://' + origin.directory})

        self._prepare_base_directory(repo['dir'])

        self._get_repository_connector(repo['type']).fork(repo)

        self._adjust_modes(repo['dir'])

        with self.env.db_transaction as db:
            id = self.manager.get_repository_id(repo['name'])
            roles = list((id, role + 's', '') for role in self.roles)
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', repo['dir']),
                 (id, 'type', repo['type']),
                 (id, 'owner', repo['owner']),
                 (id, 'description', origin.description),
                 (id, 'origin', origin.id),
                 (id, 'inherit_readers', True)] + roles)
            self.manager.reload_repositories()
        self.manager.get_repository(repo['name']).sync(None, True)
        self.update_auth_files()

    def modify(self, repo, data):
        """Modify an existing repository."""
        convert_managed_repository(self.env, repo)
        if repo.directory != data['dir']:
            shutil.move(repo.directory, data['dir'])
        with self.env.db_transaction as db:
            db.executemany(
                "UPDATE repository SET value = %s WHERE id = %s AND name = %s",
                [(data[key], repo.id, key) for key in data])
            self.manager.reload_repositories()
        if repo.directory != data['dir']:
            repo = self.get_repository(data['name'])
            repo.sync(clean=True)
        self.update_auth_files()

    def remove(self, repo, delete):
        """Remove an existing repository.

        Depending on the parameter delete this method also removes the
        repository from the filesystem. This can not be undone.
        """
        convert_managed_repository(self.env, repo)
        if delete:
            shutil.rmtree(repo.directory)
        with self.env.db_transaction as db:
            db("DELETE FROM repository WHERE id = %d" % repo.id)
            db("DELETE FROM revision WHERE repos = %d" % repo.id)
            db("DELETE FROM node_change WHERE repos = %d" % repo.id)
        self.manager.reload_repositories()
        self.update_auth_files()

    def delete_changeset(self, repo, rev, ban):
        """Delete a changeset from a managed repository, if supported.

        Depending on the parameter ban this method also marks the
        changeset to be kept out of the repository. That features needs
        special support by the used scm.
        """
        convert_managed_repository(self.env, repo)
        self._get_repository_connector(repo.type).delete_changeset(repo, rev, ban)

    def add_role(self, repo, role, subject):
        """Add a role for the given repository."""
        assert role in self.roles
        convert_managed_repository(self.env, repo)
        role_attr = '_' + role + 's'
        setattr(repo, role_attr,
                getattr(repo, role_attr) | set([subject]))
        self._update_roles_in_db(repo)

    def revoke_roles(self, repo, roles):
        """Revoke a list of `role, subject` pairs."""
        convert_managed_repository(self.env, repo)
        for role, subject in roles:
            role_attr = '_' + role + 's'
            config = getattr(repo, role_attr)
            config = config - set([subject])
            setattr(repo, role_attr,
                    getattr(repo, role_attr) - set([subject]))
        self._update_roles_in_db(repo)

    def update_auth_files(self):
        """Rewrites all configured auth files for all managed
        repositories.
        """
        types = self.get_supported_types()
        all_repositories = []
        for repo in self.manager.get_real_repositories():
            try:
                convert_managed_repository(self.env, repo)
                all_repositories.append(repo)
            except:
                pass
        for type in types:
            repos = [repo for repo in all_repositories if repo.type == type]
            self._get_repository_connector(type).update_auth_files(repos)

        authz_source_file = AuthzSourcePolicy(self.env).authz_file
        if authz_source_file:
            authz_source_path = os.path.join(self.env.path, authz_source_file)

            authz = ConfigParser()

            groups = set()
            for repo in all_repositories:
                groups |= {name for name in repo.maintainers() if name[0] == '@'}
                groups |= {name for name in repo.writers() if name[0] == '@'}
                groups |= {name for name in repo.readers() if name[0] == '@'}

            authz.add_section('groups')
            for group in groups:
                members = expand_user_set(self.env, [group])
                authz.set('groups', group[1:], ', '.join(sorted(members)))
            authenticated = sorted({u[0] for u in self.env.get_known_users()})
            authz.set('groups', 'authenticated', ', '.join(authenticated))

            for repo in all_repositories:
                section = repo.reponame + ':/'
                authz.add_section(section)
                r = repo.maintainers() | repo.writers() | repo.readers()

                def apply_user_list(users, action):
                    if not users:
                        return
                    if 'anonymous' in users:
                        authz.set(section, '*', action)
                        return
                    if 'authenticated' in users:
                        authz.set(section, '@authenticated', action)
                        return
                    for user in sorted(users):
                        authz.set(section, user, action)

                apply_user_list(r, 'r')

            self._prepare_base_directory(authz_source_path)
            with open(authz_source_path, 'wb') as authz_file:
                authz.write(authz_file)
            try:
                modes = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
                os.chmod(authz_source_path, modes)
            except:
                pass

    ### Private methods
    def _get_repository_connector(self, repo_type):
        """Get the matching connector with maximum priority."""
        return max(((connector, type, prio) for connector in self.connectors
                    for (type, prio) in connector.get_supported_types()
                    if prio >= 0 and type == repo_type),
                   key=lambda x: x[2])[0]

    def _prepare_base_directory(self, directory):
        """Create the base directories and set the correct modes."""
        base = os.path.dirname(directory)
        original_umask = os.umask(0)
        try:
            os.makedirs(base, stat.S_IRWXU | stat.S_IRWXG)
        except OSError, e:
            if e.errno == errno.EEXIST and os.path.isdir(base):
                pass
            else:
                raise
        finally:
            os.umask(original_umask)

    def _adjust_modes(self, directory):
        """Set modes 770 and 660 for directories and files."""
        try:
            os.chmod(directory, stat.S_IRWXU | stat.S_IRWXG)
            fmodes = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP
            dmodes = stat.S_IRWXU | stat.S_IRWXG
            for subdir, dirnames, filenames in os.walk(directory):
                for dirname in dirnames:
                    os.chmod(os.path.join(subdir, dirname), dmodes)
                    for filename in filenames:
                        os.chmod(os.path.join(subdir, filename), fmodes)
        except OSError, e:
            raise TracError(_("Failed to adjust file modes: " + str(e)))

    def _update_roles_in_db(self, repo):
        """Make the current roles persistent in the database."""
        roles = {}
        for role in self.roles:
            roles[role] = getattr(repo, '_' + role + 's')
        with self.env.db_transaction as db:
            db.executemany(
                "UPDATE repository SET value = %s WHERE id = %s AND name = %s",
                [(','.join(roles[role]), repo.id, role + 's')
                 for role in self.roles])

def convert_managed_repository(env, repo):
    """Convert a given repository into a `ManagedRepository`."""

    class ManagedRepository(repo.__class__):
        """A repository managed by the new `RepositoryManager`.

        This repository class inherits from the original class of the
        given repository and adds fields needed by the manager.

        Trying to convert a repository that was added via the original
        `RepositoryAdminPanel` raises an exception and can therefore
        be used to easily check if we are working with a manageable
        repository.
        """

        id = None
        owner = None
        type = None
        description = None
        is_fork = False
        is_forkable = False
        directory = None
        _owner_is_maintainer = False
        _maintainers = set()
        _writers = set()
        _readers = set()

        def maintainers(self):
            if self._owner_is_maintainer:
                return self._maintainers | set([self.owner])
            return self._maintainers

        def writers(self):
            return self._writers | set([self.owner])

        def readers(self):
            return self._readers | set([self.owner])

    class ForkedRepository(ManagedRepository):
        """A local fork of a `ManagedRepository`.

        This repository class inherits from the original class of the
        given repository and adds fields and methods needed by the
        manager and for e.g. pull requests.
        """

        origin = None
        inherit_readers = False

        def get_youngest_common_ancestor(self, rev):
            """Goes back in the repositories history starting from
            `rev` until it finds a revision that also exists in the
            origin of this fork.
            """
            nodes = [rev]
            while len(nodes):
                node = nodes.pop(0)

                try:
                    self.origin.get_changeset(node)
                except:
                    pass
                else:
                    return node

                for ancestor in self.parent_revs(node):
                    nodes.append(ancestor)

            return None

        def readers(self):
            readers = ManagedRepository.readers(self)
            if self.inherit_readers:
                return readers | self.origin.maintainers()
            return readers

    def _get_role(db, role):
        """Get the set of users that have the given `role` on this
        repository.
        """
        result = db("""SELECT value FROM repository
                       WHERE name = '%s' AND id = %d
                       """ % (role + 's', repo.id))[0][0]
        if result:
            return set(result.split(','))
        return set()

    if repo.__class__ is not ManagedRepository:
        trac_rm = TracRepositoryManager(env)
        repo.id = trac_rm.get_repository_id(repo.reponame)
        rm = RepositoryManager(env)
        with env.db_transaction as db:
            result = db("""SELECT value FROM repository
                           WHERE name = 'owner' AND id = %d
                           """ % repo.id)
            if not result:
                raise TracError(_("Not a managed repository"))

            repo.__class__ = ManagedRepository
            repo.owner = result[0][0]
            for role in rm.roles:
                role_attr = '_' + role + 's'
                setattr(repo, role_attr,
                        getattr(repo, role_attr) | _get_role(db, role))
        repo._owner_is_maintainer = rm.owner_as_maintainer

        info = trac_rm.get_all_repositories().get(repo.reponame)
        repo.type = info['type']
        repo.description = info.get('description')
        repo.is_forkable = repo.type in rm .get_forkable_types()
        repo.directory = info['dir']

        with env.db_transaction as db:
            result = db("""SELECT value FROM repository
                           WHERE name = 'name' AND
                                 id = (SELECT value FROM repository
                                       WHERE name = 'origin' AND id = %d)
                           """ % repo.id)
            if not result:
                return

            repo.__class__ = ForkedRepository
            repo.is_fork = True
            repo.origin = rm.get_repository(result[0][0], True)
            if repo.origin is None:
                raise TracError(_("Origin of previously forked repository "
                                  "does not exist anymore"))
            result = db("""SELECT value FROM repository
                           WHERE name = 'inherit_readers' AND id = %d
                           """ % repo.id)
            repo.inherit_readers = as_bool(result[0][0])

def expand_user_set(env, users):
    """Replaces all groups by their users until only users are left."""
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
