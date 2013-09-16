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

    def get_supported_types(self):
        types = set(repo_type for connector in self.connectors
                    for (repo_type, priority) in connector.get_supported_types() or []
                    if priority >= 0)
        return list(types & set(TracRepositoryManager(self.env).get_supported_types()))

    def get_forkable_types(self):
        return list(repo_type for repo_type in self.get_supported_types() if self.can_fork(repo_type))

    def can_fork(self, repository_type):
        return self.get_repository_connector(repository_type).can_fork(repository_type)

    def create(self, repository):
        rm = TracRepositoryManager(self.env)
        if rm.get_repository(repository['name']) or os.path.lexists(repository['directory']):
            raise TracError(_('Repository or directory already exists'))

        self.prepare_base_directory(repository['directory'])

        self.get_repository_connector(repository['type']).create(repository)

        self.adjust_modes(repository['directory'])

        with self.env.db_transaction as db:
            repository['id'] = rm.get_repository_id(repository['name'])
            db.executemany('INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)',
                           [(repository['id'], 'dir', repository['directory']),
                            (repository['id'], 'type', repository['type']),
                            (repository['id'], 'owner', repository['owner'])])
            rm.reload_repositories()
        rm.get_repository(repository['name']).sync(None, True)

    def fork_local(self, repository):
        rm = TracRepositoryManager(self.env)
        if rm.get_repository(repository['name']) or os.path.lexists(repository['directory']):
            raise TracError(_('Repository or directory already exists.'))

        origin = rm.get_all_repositories().get(repository['origin'])
        if not origin:
            raise TracError(_('Origin for local fork does not exist.'))
        if origin['type'] != repository['type']:
            raise TracError(_('Fork of local repository must have same type as origin'))
        repository.update({'origin_url': 'file://' + origin['dir']})

        self.get_repository_connector(repository['type']).fork(repository)

        self.adjust_modes(repository['directory'])

        with self.env.db_transaction as db:
            repository['id'] = rm.get_repository_id(repository['name'])
            db.executemany('INSERT INTO repository (id, name, value) VALUES (%s, %s, %s)',
                           [(repository['id'], 'dir', repository['directory']),
                            (repository['id'], 'type', repository['type']),
                            (repository['id'], 'owner', repository['owner']),
                            (repository['id'], 'origin', origin['id'])])
            rm.reload_repositories()
        rm.get_repository(repository['name']).sync(None, True)

    def remove(self, repository, delete):
        convert_managed_repository(self.env, repository)
        rm = TracRepositoryManager(self.env)
        repoid = rm.get_repository_id(repository.reponame)
        directory = rm.get_all_repositories().get(repository.reponame).get('dir')
        with self.env.db_transaction as db:
            db('DELETE FROM repository WHERE id = %d' % repoid)
        if delete:
            shutil.rmtree(directory)
        rm.reload_repositories()

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


def convert_managed_repository(env, repos):

    class ManagedRepository(repos.__class__):

        owner = None
        is_forkable = None

    if repos.__class__ is not ManagedRepository:
        repos.__class__ = ManagedRepository
        trac_rm = TracRepositoryManager(env)
        repoid = trac_rm.get_repository_id(repos.reponame)
        repos.owner = None
        with env.db_transaction as db:
            result = db("SELECT value FROM repository WHERE name = 'owner' AND id = %d" % repoid)
            if not result:
                raise TracError(_('Not a managed repository'))

            repos.owner = result[0][0]
        repotype = trac_rm.get_all_repositories().get(repos.reponame)['type']
        repos.is_forkable = repotype in RepositoryManager(env).get_forkable_types()

def convert_forked_repository(env, repos):

    class ForkedRepository(repos.__class__):

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

    convert_managed_repository(repos)
    if repos.__class__ is not ForkedRepository:
        repos.__class__ = ForkedRepository
        rm = TracRepositoryManager(env)
        repoid = rm.get_repository_id(repos.reponame)
        repos.origin = None
        with env.db_transaction as db:
            result = db("SELECT value FROM repository WHERE name = 'name' AND id = (SELECT value FROM repository WHERE name = 'origin' AND id = %d)" % repoid)
            env.log.debug('result:')
            env.log.debug(result)
            if not result:
                raise TracError(_('Not a forked repository'))

            repos.origin = rm.get_repository(result[0][0])
            if repos.origin is None:
                raise TracError(_('Origin of previously forked repository does not exist anymore'))

    assert repos.origin
