from api import *

from trac.core import *
from trac.perm import PermissionSystem
from trac.admin import AdminCommandError, IAdminCommandProvider, get_dir_list
from trac.versioncontrol.api import DbRepositoryProvider
from trac.versioncontrol.api import RepositoryManager as TracRepositoryManager
from trac.versioncontrol.admin import RepositoryAdminPanel
from trac.util.text import printout, print_table
from trac.util.translation import _

from itertools import izip_longest

class RepositoryAdmin(Component):
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

    implements(IAdminCommandProvider)

    #### IAdminCommandProvider methods
    def get_admin_commands(self):
        yield ('repository create', '<repos> <type> <owner> [dir]',
               "Create a new managed repository",
               self._complete_create, self._do_create)
        yield ('repository fork', '<repos> <type> [dir]',
               "Fork an existing managed repository",
               self._complete_create, self._do_fork)
        yield ('repository remove_managed', '<repos>',
               """Remove a managed repository

               Using the built-in command `repository remove' on a
               managed repository might leave unusable entries in the db
               and will not delete the repository from the file system.
               """,
               self._complete_remove_managed, self._do_remove)
        attrs = set(DbRepositoryProvider(self.env).repository_attrs)
        yield ('repository set_managed', '<repos> <key> <value>',
               """Set an attribute of a managed repository

               Using the built-in command `repository set' on a managed
               repository might corrupt the db.

               The following keys are supported: %s
               """ % ", ".join(attrs - set(['type'])),
               self._complete_set_managed, self._do_set)
        yield ('repository list_managed', '',
               "List only managed repositories",
               None, self._do_list_managed)
        yield ('repository list_unmanaged', None,
               "List only unmanaged repositories",
               None, self._do_list_unmanaged)
        yield ('role add', '<repos> <role> <user> [user] [...]',
               """Add a new role

               The following roles are supported: %s
               """ % ", ".join(RepositoryManager(self.env).roles),
               self._complete_role_add, self._do_role_add)
        yield ('role remove', '<repos> <role> <user> [user] [...]',
               "Remove an existing role",
               self._complete_role_remove, self._do_role_remove)
        yield ('role export', '[file]',
               "Export repository roles to a file or stdout as CSV",
               None, self._do_role_export)
        yield ('role import', '[file]',
               "Import repository roles from a file or stdin as CSV",
               self._complete_role_import, self._do_role_import)
        yield ('role list', '<repos>',
               "List roles for given repository",
               self._complete_role_list, self._do_role_list)
        yield ('write_auth_files', '',
               "Rewrites all configured auth files for all managed"
               "repositories",
               None, self._do_write_auth_files)

    ### Private methods
    def _complete_create(self, args):
        if len(args) == 2:
            return RepositoryManager(self.env).get_supported_types()
        if len(args) == 3:
            return {u[0] for u in self.env.get_known_users()}

    def _complete_managed_repositories(self, args):
        return RepositoryManager(self.env).get_managed_repositories()

    def _complete_remove_managed(self, args):
        if len(args) == 1:
            return self._complete_managed_repositories(args)

    def _complete_set_managed(self, args):
        if len(args) == 1:
            return self._complete_managed_repositories(args)
        elif len(args) == 2:
            return DbRepositoryProvider(self.env).repository_attrs

    def _complete_role_add(self, args):
        if len(args) == 1:
            return self._complete_managed_repositories(args)
        elif len(args) == 2:
            return RepositoryManager(self.env).roles
        if len(args) >= 3:
            ps = PermissionSystem(self.env)
            groups = set('@' + perm[1] for perm in ps.get_all_permissions()
                         if not perm[1].isupper())
            return groups | {u[0] for u in self.env.get_known_users()}

    def _complete_role_remove(self, args):
        if len(args) == 1:
            return self._complete_managed_repositories(args)
        elif len(args) == 2:
            return RepositoryManager(self.env).roles
        if len(args) >= 3:
            repos = RepositoryManager(self.env).get_repository(args[0], True)
            if repos:
                return getattr(repos, '_' + args[1] + 's')

    def _complete_role_import(self, args):
        if len(args) == 1:
            return get_dir_list(args[-1])

    def _complete_role_list(self, args):
        if len(args) == 1:
            return self._complete_managed_repositories(args)

    def _do_create(self, name, type, owner, dir=None):
        rm = RepositoryManager(self.env)
        base_directory = rm.get_base_directory(type)
        directory = os.path.join(base_directory, dir or name)

        if os.path.lexists(directory):
            raise AdminCommandError(_('Directory "%(name)s" already exists',
                                      name=directory))

        rap = RepositoryAdminPanel(self.env)
        prefixes = [os.path.join(self.env.path, prefix)
                    for prefix in rap.allowed_repository_dir_prefixes]
        if prefixes and not any(is_path_below(directory, prefix)
                                for prefix in prefixes):
            add_warning(req, _("The repository directory must be located "
                               "below one of the following directories: "
                               "%(dirs)s", dirs=', '.join(prefixes)))

            if rm.get_repository(name):
                raise AdminCommandError(_('Repository "%(name)s" already '
                                          'exists', name=name))
        repo = {'name': name,
                'type': type,
                'owner': owner,
                'dir': directory}
        rm.create(repo)

    def _do_fork(self):
        printout("fork")

    def _do_remove(self):
        printout("remove")

    def _do_set(self):
        printout("set")

    def _do_list_managed(self):
        rm = RepositoryManager(self.env)
        trm = TracRepositoryManager(self.env)
        values = []
        for (reponame, info) in sorted(trm.get_all_repositories().iteritems()):
            alias = ''
            if 'alias' in info:
                alias = info['alias'] or '(default)'
            try:
                repos = rm.get_repository(reponame, True)
                values.append((reponame or '(default)', info.get('type', ''),
                               alias, repos.owner, info.get('dir', '')))
            except:
                pass
        print_table(values,
                    [_("Name"), _("Type"), _("Alias"), _("Owner"),
                     _("Directory")])

    def _do_list_unmanaged(self):
        rm = RepositoryManager(self.env)
        trm = TracRepositoryManager(self.env)
        values = []
        for (reponame, info) in sorted(trm.get_all_repositories().iteritems()):
            alias = ''
            if 'alias' in info:
                alias = info['alias'] or '(default)'
            try:
                repos = rm.get_repository(reponame, True)
            except:
                values.append((reponame or '(default)', info.get('type', ''),
                               alias, info.get('dir', '')))
        print_table(values, [_('Name'), _('Type'), _('Alias'), _('Directory')])

    def _do_role_add(self, repos, role, user, *users):
        rm = RepositoryManager(self.env)
        for subject in set([user]) | set(users):
            rm.add_role(rm.get_repository(repos, True), role, subject)
        rm.update_auth_files()

    def _do_role_remove(self, repos, role, user, *users):
        rm = RepositoryManager(self.env)
        roles = ((role, subject) for subject in set([user]) | set(users))
        rm.revoke_roles(rm.get_repository(repos, True), roles)
        rm.update_auth_files()

    def _do_role_export(self, file=None):
        printout("role export")

    def _do_role_import(self, file=None):
        printout("role import")

    def _do_role_list(self, repos):
        repository = RepositoryManager(self.env).get_repository(repos, True)
        if not repository:
            raise AdminCommandError(_("Not a managed repository"))

        columns = []
        if repository.is_forkable:
            columns.append(_("Maintainers"))
            values = list(izip_longest(sorted(repository.maintainers()),
                                       sorted(repository.writers()),
                                       sorted(repository.readers()),
                                       fillvalue=''))
        else:
            values = list(izip_longest(sorted(repository.writers()),
                                       sorted(repository.readers()),
                                       fillvalue=''))
        columns.append(_("Writers"))
        columns.append(_("Readers"))
        print_table(values, columns)

    def _do_write_auth_files(self):
        RepositoryManager(self.env).update_auth_files()
