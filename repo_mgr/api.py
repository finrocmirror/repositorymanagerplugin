from trac.core import *
from trac.versioncontrol.api import RepositoryManager as TracRepositoryManager
from trac.util.translation import _

import os, errno, stat, shutil

class IAdministrativeRepositoryConnector(Interface):
    """Provide support for a specific version control system

    Instead of Trac's usual IRepositoyConnector interface for navigating
    repositories of specific version control systems, this one is used
    for more administrative tasks like creation, forking, renaming or
    deleting repositories.
    """

    error = None

    def get_supported_types():
        """Return the types of version control systems that are supported

        Yields `(repository type, priority)` pairs.

        If multiple provider match a given type, the `priority` is used to
        choose between them (highest number is highest priority).

        If the `priority` returned is negative, this indicates that the
        connector for the given `repository type` indeed exists but can't be
        used for some reason. The `error` property can then be used to
        store an error message or exception relevant to the problem detected.
        """

    def can_fork(repository_type):
        """Return whether forking is supported by the connector"""

    def create(repository):
        """Create a new empty repository with given attributes"""

    def fork(repository):
        """Fork from `origin_url` in the given dict"""

class RepositoryManager(Component):
    """
    """

    connectors = ExtensionPoint(IAdministrativeRepositoryConnector)

    manager = None

    def __init__(self):
        self.manager = TracRepositoryManager(self.env)

    def get_supported_types(self):
        types = set(repo_type for connector in self.connectors
                    for (repo_type, priority) in connector.get_supported_types() or []
                    if priority >= 0)
        return list(types & set(self.manager.get_supported_types()))

    def get_forkable_types(self):
        return list(repo_type for repo_type in self.get_supported_types() if self.can_fork(repo_type))

    def can_fork(self, repository_type):
        return self.get_repository_connector(repository_type).can_fork(repository_type)

    def get_forkable_repositories(self):
        repositories = self.manager.get_all_repositories()
        for key in repositories:
            if repositories[key]['type'] in self.get_forkable_types():
                yield repositories[key]['name']

    def get_repository(self, name, cast_to_managed=False):
        repository = self.manager.get_repository(name)
        if repository and cast_to_managed:
            convert_managed_repository(self.env, repository)
        return repository

    def get_repository_by_path(self, path):
        return self.manager.get_repository_by_path(path)

    def create(self, repository):
        if self.get_repository(repository['name']) or os.path.lexists(repository['directory']):
            raise TracError(_('Repository or directory already exists'))

        self.prepare_base_directory(repository['directory'])

        self.get_repository_connector(repository['type']).create(repository)

        self.adjust_modes(repository['directory'])

        with self.env.db_transaction as db:
            repoid = self.manager.get_repository_id(repository['name'])
            db.executemany('INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)',
                           [(repoid, 'dir', repository['directory']),
                            (repoid, 'type', repository['type']),
                            (repoid, 'owner', repository['owner'])])
            self.manager.reload_repositories()
        self.manager.get_repository(repository['name']).sync(None, True)

    def fork_local(self, repository):
        if self.get_repository(repository['name']) or os.path.lexists(repository['directory']):
            raise TracError(_('Repository or directory already exists.'))

        origin = self.get_repository(repository['origin'])
        if not origin:
            raise TracError(_('Origin for local fork does not exist.'))
        if origin.type != repository['type']:
            raise TracError(_('Fork of local repository must have same type as origin.'))
        repository.update({'origin_url': 'file://' + origin.directory})

        self.get_repository_connector(repository['type']).fork(repository)

        self.adjust_modes(repository['directory'])

        with self.env.db_transaction as db:
            repoid = self.manager.get_repository_id(repository['name'])
            db.executemany('INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)',
                           [(repoid, 'dir', repository['directory']),
                            (repoid, 'type', repository['type']),
                            (repoid, 'owner', repository['owner']),
                            (repoid, 'origin', origin.id)])
            self.manager.reload_repositories()
        self.manager.get_repository(repository['name']).sync(None, True)

    def modify(self, repository, data):
        convert_managed_repository(self.env, repository)
        if repository.directory != data['directory']:
            shutil.move(repository.directory, data['directory'])
        with self.env.db_transaction as db:
            db.executemany('UPDATE repository SET value = %s WHERE id = %s AND name = %s',
                           [(data['name'], repository.id, 'name'),
                            (data['directory'], repository.id, 'dir')])
            self.manager.reload_repositories()

    def remove(self, repository, delete):
        convert_managed_repository(self.env, repository)
        with self.env.db_transaction as db:
            db('DELETE FROM repository WHERE id = %d' % repository.id)
            self.manager.reload_repositories()
        if delete:
            shutil.rmtree(repository.directory)

    ### Private methods
    def get_repository_connector(self, repository_type):
        return max(((connector, repo_type, priority) for connector in self.connectors
                    for (repo_type, priority) in connector.get_supported_types()
                    if priority >= 0 and repo_type == repository_type),
                   key=lambda x: x[2])[0]

    def prepare_base_directory(self, directory):
        base = os.path.dirname(directory)
        try:
            os.makedirs(base)
            os.chmod(base, stat.S_IRWXU | stat.S_IRWXG)
        except OSError, e:
            if e.errno == errno.EEXIST and os.path.isdir(base):
                pass
            else:
                raise

    def adjust_modes(self, directory):
        try:
            os.chmod(directory, stat.S_IRWXU | stat.S_IRWXG)
            for subdir, dirnames, filenames in os.walk(directory):
                for dirname in dirnames:
                    os.chmod(os.path.join(subdir, dirname), stat.S_IRWXU | stat.S_IRWXG)
                    for filename in filenames:
                        os.chmod(os.path.join(subdir, filename), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)
        except OSError, e:
            raise TracError(_('Failed to adjust file modes: ' + str(e)))


def convert_managed_repository(env, repository):

    class ManagedRepository(repository.__class__):

        id = None
        owner = None
        type = None
        is_forkable = None
        directory = None

    if repository.__class__ is not ManagedRepository:
        repository.__class__ = ManagedRepository
        trac_rm = TracRepositoryManager(env)
        repository.id = trac_rm.get_repository_id(repository.reponame)
        with env.db_transaction as db:
            result = db("SELECT value FROM repository WHERE name = 'owner' AND id = %d" % repository.id)
            if not result:
                raise TracError(_('Not a managed repository'))
            repository.owner = result[0][0]

        info = trac_rm.get_all_repositories().get(repository.reponame)
        repository.type = info['type']
        repository.is_forkable = repository.type in RepositoryManager(env).get_forkable_types()
        repository.directory = info['dir']

def convert_forked_repository(env, repository):

    class ForkedRepository(repository.__class__):

        origin = None

        def get_youngest_common_ancestor(self, rev):
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

    convert_managed_repository(repository)
    if repository.__class__ is not ForkedRepository:
        repository.__class__ = ForkedRepository
        with env.db_transaction as db:
            result = db("SELECT value FROM repository WHERE name = 'name' AND id = (SELECT value FROM repository WHERE name = 'origin' AND id = %d)" % repository.id)
            if not result:
                raise TracError(_('Not a forked repository'))

            repository.origin = TracRepositoryManager(env).get_repository(result[0][0])
            if repository.origin is None:
                raise TracError(_('Origin of previously forked repository does not exist anymore'))

    assert repository.origin
