from trac.core import *
from trac.versioncontrol.api import RepositoryManager as TracRepositoryManager
from trac.util.translation import _

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

    def create(repository):
        """Create a new empty repository with given attributes."""

    def fork(repository):
        """Fork from `origin_url` in the given dict."""

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

    connectors = ExtensionPoint(IAdministrativeRepositoryConnector)

    manager = None

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

    def get_forkable_repositories(self):
        """Return a dictionary of repository information, indexed by
        name and including only repositories that can be forked."""
        repositories = self.manager.get_all_repositories()
        for key in repositories:
            if repositories[key]['type'] in self.get_forkable_types():
                yield repositories[key]['name']

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

    def get_repository_by_path(self, path):
        """Retrieve a matching `Repository` for the given path."""
        return self.manager.get_repository_by_path(path)

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
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', repo['dir']),
                 (id, 'type', repo['type']),
                 (id, 'owner', repo['owner'])])
            self.manager.reload_repositories()
        self.manager.get_repository(repo['name']).sync(None, True)

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

        self._get_repository_connector(repo['type']).fork(repo)

        self._adjust_modes(repo['dir'])

        with self.env.db_transaction as db:
            id = self.manager.get_repository_id(repo['name'])
            db.executemany(
                "INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)",
                [(id, 'dir', repo['dir']),
                 (id, 'type', repo['type']),
                 (id, 'owner', repo['owner']),
                 (id, 'origin', origin.id)])
            self.manager.reload_repositories()
        self.manager.get_repository(repo['name']).sync(None, True)

    def modify(self, repo, data):
        """Modify an existing repository."""
        convert_managed_repository(self.env, repo)
        if repo.directory != data['dir']:
            shutil.move(repo.directory, data['dir'])
        with self.env.db_transaction as db:
            db.executemany(
                "UPDATE repository SET value = %s WHERE id = %s AND name = %s",
                [(data['name'], repo.id, 'name'),
                 (data['dir'], repo.id, 'dir')])
            self.manager.reload_repositories()

    def remove(self, repo, delete):
        """Remove an existing repository.

        Depending on the parameter delete this method  also removes the
        repository from the filesystem. This can not be undone.
        """
        convert_managed_repository(self.env, repo)
        with self.env.db_transaction as db:
            db("DELETE FROM repository WHERE id = %d" % repo.id)
            self.manager.reload_repositories()
        if delete:
            shutil.rmtree(repo.directory)

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
        try:
            os.makedirs(base)
            os.chmod(base, stat.S_IRWXU | stat.S_IRWXG)
        except OSError, e:
            if e.errno == errno.EEXIST and os.path.isdir(base):
                pass
            else:
                raise

    def _adjust_modes(self, directory):
        """Set modes 750 and 640 for directories and files."""
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
        is_forkable = None
        directory = None

    if repo.__class__ is not ManagedRepository:
        repo.__class__ = ManagedRepository
        trac_rm = TracRepositoryManager(env)
        repo.id = trac_rm.get_repository_id(repo.reponame)
        with env.db_transaction as db:
            result = db("""SELECT value FROM repository
                           WHERE name = 'owner' AND id = %d
                           """ % repo.id)
            if not result:
                raise TracError(_("Not a managed repository"))
            repo.owner = result[0][0]

        info = trac_rm.get_all_repositories().get(repo.reponame)
        repo.type = info['type']
        rm = RepositoryManager(env)
        repo.is_forkable = repo.type in rm .get_forkable_types()
        repo.directory = info['dir']

def convert_forked_repository(env, repo):
    """Convert a given repository into a `ForkedRepository`."""

    class ForkedRepository(repo.__class__):
        """A local fork of a `ManagedRepository`.

        This repository class inherits from the original class of the
        given repository and adds fields and methods needed by the
        manager and for e.g. pull requests.

        Trying to convert a repository that was not forked via the new
        `RepositoryManager` raises an exception and can therefore
        be used to easily check if we are working with a forked
        repository.
        """

        origin = None

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

    convert_managed_repository(env, repo)
    if repo.__class__ is not ForkedRepository:
        repo.__class__ = ForkedRepository
        with env.db_transaction as db:
            result = db("""SELECT value FROM repository
                           WHERE name = 'name' AND
                                 id = (SELECT value FROM repository
                                       WHERE name = 'origin' AND id = %d)
                           """ % repo.id)
            if not result:
                raise TracError(_("Not a forked repository"))

            trac_rm = TracRepositoryManager(env)
            repo.origin = trac_rm.get_repository(result[0][0])
            if repo.origin is None:
                raise TracError(_("Origin of previously forked repository "
                                  "does not exist anymore"))

    assert repo.origin
