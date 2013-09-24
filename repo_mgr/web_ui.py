from api import *

from trac.core import *
from trac.perm import IPermissionRequestor, PermissionError, PermissionSystem
from trac.web import IRequestHandler, IRequestFilter
from trac.web.auth import LoginModule
from trac.web.chrome import INavigationContributor, ITemplateProvider, \
                            add_ctxtnav, add_stylesheet, \
                            add_notice, add_warning
from trac.versioncontrol.admin import RepositoryAdminPanel
from trac.util import is_path_below
from trac.util.translation import _, tag_
from trac.util.text import normalize_whitespace, \
                           unicode_to_base64, unicode_from_base64
from trac.config import BoolOption, PathOption

from genshi.builder import tag

import os
import re

class RepositoryManagerModule(Component):
    """The `RepositoryManager`'s user interface."""

    implements(IPermissionRequestor, IRequestHandler, ITemplateProvider)

    base_dir = PathOption('repository-manager', 'base_dir', 'repositories',
                          doc="""The base folder in which repositories will be
                                 created.
                                 """)
    restrict_dir = BoolOption('repository-manager', 'restrict_dir', True,
                              doc="""Always use the repository name as
                                     directory name. Disables some form
                                     elements.
                                     """)

    ### IPermissionRequestor methods
    def get_permission_actions(self):
        actions = ['REPOSITORY_CREATE', 'REPOSITORY_FORK']
        return actions + [('REPOSITORY_ADMIN', actions)]

    ### IRequestHandler methods
    def match_request(self, req):
        match = re.match(r'^/repository(/(\w+))?(/(\w+))?', req.path_info)
        if match:
            _, action, _, reponame = match.groups()
            req.args['action'] = action or 'list'
            req.args['reponame'] = reponame or None
            return True

    def process_request(self, req):
        RepositoryManager(self.env).update_auth_files()
        action = req.args.get('action', 'list')
        if action == 'list':
            req.redirect(req.href.browser())

        data = {'action': action,
                'restrict_dir': self.restrict_dir,
                'possible_owners': self._get_possible_owners(req),
                'referer': req.args.get('referer', req.get_header('Referer')),
                'unicode_to_base64': unicode_to_base64}

        if action == 'create':
            self._process_create_request(req, data)
        elif action == 'fork':
            repo = self._get_checked_repository(req, req.args.get('reponame'),
                                                False, 'REPOSITORY_FORK')
            if not repo.is_forkable:
                raise TracError(_("Repository is not forkable"))
            self._process_fork_request(req, data)
        elif action == 'modify':
            self._process_modify_request(req, data)
        elif action == 'remove':
            self._process_remove_request(req, data)

        possible_owners = None
        if 'REPOSITORY_ADMIN' in req.perm:
            possible_owners = {u[0] for u in self.env.get_known_users()}

#        add_stylesheet(req, 'common/css/browser.css')
        add_stylesheet(req, 'common/css/admin.css')
        return 'repository.html', data, None

    ### ITemplateProvider methods
    def get_templates_dirs(self):
        from pkg_resources import resource_filename
        return [resource_filename(__name__, 'templates')]

    def get_htdocs_dirs(self):
        from pkg_resources import resource_filename
        return [('hw', resource_filename(__name__, 'htdocs'))]

    ### Private methods
    def _process_create_request(self, req, data):
        """Create a new repository.

        Depending on the content of `req.args` either create a new empty
        repository, fork a locally existing one or fork a remote
        repository.
        """
        req.perm.require('REPOSITORY_CREATE')

        rm = RepositoryManager(self.env);

        repository = self._get_repository_data_from_request(req, 'create_')
        remote_fork = self._get_repository_data_from_request(req, 'remote_')

        if req.args.get('create'):
            self._create(req, repository, rm.create)

        elif req.args.get('fork_remote'):
            self._create(req, remote_fork, rm.fork_remote)

        self._process_fork_request(req, data)

        data.update({'title': _("Create Repository"),
                     'supported_repository_types': rm.get_supported_types(),
                     'forkable_repository_types': rm.get_forkable_types(),
                     'forkable_repositories': rm.get_forkable_repositories(),
                     'repository': repository,
                     'local_fork': data.get('local_fork', {}),
                     'remote_fork': remote_fork})

    def _process_fork_request(self, req, data):
        """Fork an existing repository."""
        origin_name = req.args.get('local_origin', req.args.get('reponame'))
        local_fork = self._get_repository_data_from_request(req, 'local_')
        local_fork['origin'] = req.args.get('local_origin')

        if req.args.get('fork_local'):
            origin = self._get_checked_repository(req, local_fork['origin'],
                                                  False, 'REPOSITORY_FORK')
            local_fork.update({'type': origin.type})
            rm = RepositoryManager(self.env);
            self._create(req, local_fork, rm.fork_local)

        data.update({'title': _("Fork Repository %(name)s", name=origin_name),
                     'local_fork': local_fork})

    def _process_modify_request(self, req, data):
        """Modify an existing repository."""
        repo = self._get_checked_repository(req, req.args.get('reponame'))

        base_directory = self._get_base_directory(repo.type)
        prefix_length = len(base_directory)
        if prefix_length > 0:
            prefix_length += 1

        req.args['name'] = req.args.get('name', repo.reponame)
        req.args['type'] = repo.type
        req.args['dir'] = req.args.get('dir', repo.directory[prefix_length:])
        req.args['owner'] = req.args.get('owner', repo.owner)
        new = self._get_repository_data_from_request(req)

        rm = RepositoryManager(self.env)
        if req.args.get('modify'):
            if self._check_and_update_repository(req, new):
                rm.modify(repo, new)
                link = tag.a(repo.reponame, href=req.href.browser(new['name']))
                add_notice(req, tag_('The repository "%(link)s" has been '
                                     'modified.', link=link))
                req.redirect(req.href.repository())
        elif self._process_role_adding(req, repo):
            req.redirect(req.href(req.path_info))
        elif req.args.get('revoke'):
            selection = req.args.get('selection')
            if selection:
                if not isinstance(selection, list):
                    selection = [selection]
                roles = [role.split(':') for role in selection]
                decode = unicode_from_base64
                roles = [(decode(role[0]), decode(role[1])) for role in roles]
                rm.revoke_roles(repo, roles)
                req.redirect(req.href(req.path_info))
        elif req.args.get('cancel'):
            LoginModule(self.env)._redirect_back(req)

        repo_link = tag.a(repo.reponame, href=req.href.browser(repo.reponame))
        data.update({'title': tag_("Modify Repository %(link)s",
                                   link=repo_link),
                     'repository': repo,
                     'new': new,
                     'users': self._get_users(),
                     'groups': self._get_groups()})

    def _process_remove_request(self, req, data):
        """Remove an existing repository."""
        repo = self._get_checked_repository(req, req.args.get('reponame'))

        if req.args.get('confirm'):
            RepositoryManager(self.env).remove(repo, req.args.get('delete'))
            add_notice(req, _('The repository "%(name)s" has been removed.',
                              name=repo.reponame))
            req.redirect(req.href.repository())
        elif req.args.get('cancel'):
            LoginModule(self.env)._redirect_back(req)

        data.update({'title': _("Remove Repository"),
                     'repository': repo})

    def _get_checked_repository(self, req, name, owner=True, permission=None):
        """Check if a repository exists and the user is the owner and
        has the given permission. Finally return the repository.
        """
        if not name:
            raise TracError(_("Repository not specified"))

        rm = RepositoryManager(self.env)
        repository = rm.get_repository(name, True)
        if not repository:
            raise TracError(_('Repository "%(name)s" does not exist.',
                              name=name))

        if owner and not (repository.owner == req.authname or
                          'REPOSITORY_ADMIN' in req.perm):
            message = _('You (%(user)s) are not the owner of "%(name)s"',
                        user=req.authname, name=name)
            raise PermissionError(message)

        if permission and not permission in req.perm:
            raise PermissionError(permission, None, self.env)

        return repository

    def _get_base_directory(self, type):
        """Get the base directory for the given repository type."""
        return os.path.join(self.env.path, self.base_dir, type)

    def _create(self, req, repo, creator):
        """Check if a repository can be created and create it using the
        given creator function.
        """
        if not repo['name']:
            add_warning(req, _("Missing arguments to create a repository."))
        elif self._check_and_update_repository(req, repo):
            creator(repo)
            link = tag.a(repo['name'], href=req.href.browser(repo['name']))
            add_notice(req, tag_('The repository "%(link)s" has been created.',
                                 link=link))
            req.redirect(req.href(req.path_info))

    def _check_and_update_repository(self, req, repo):
        """Check if a repository is valid, does not already exist,
        update the dict and add a warning message otherwise.
        """
        if not repo['dir']:
            add_warning(req, _("The directory is missing."))
            return False

        base_directory = self._get_base_directory(repo['type'])
        directory = os.path.join(base_directory, repo['dir'])

        if os.path.lexists(directory):
            add_warning(req, _('Directory "%(name)s" already exists',
                               name=directory))
            return False

        rap = RepositoryAdminPanel(self.env)
        prefixes = [os.path.join(self.env.path, prefix)
                    for prefix in rap.allowed_repository_dir_prefixes]
        if prefixes and not any(is_path_below(directory, prefix)
                                for prefix in prefixes):
            add_warning(req, _("The repository directory must be located "
                               "below one of the following directories: "
                               "%(dirs)s", dirs=', '.join(prefixes)))
            return False

        rm = RepositoryManager(self.env)
        if rm.get_repository(repo['name']):
            add_warning(req, _('Repository "%(name)s" already exists',
                               name=repo['name']))
            return False

        repo.update({'dir': directory})
        return True

    def _get_possible_owners(self, req):
        """Get the list of known users if `REPOSITORY_ADMIN` permission is
        available. None otherwise.
        """
        if 'REPOSITORY_ADMIN' in req.perm:
            return {u[0] for u in self.env.get_known_users()}
        return None

    def _get_users(self):
        """Get the list of known users."""
        return {u[0] for u in self.env.get_known_users()}

    def _get_groups(self):
        """Get the list of known groups."""
        ps = PermissionSystem(self.env)
        result = list(set(perm[1] for perm in ps.get_all_permissions()
                      if not perm[1].isupper()))
        return result

    def _process_role_adding(self, req, repo):
        rm = RepositoryManager(self.env)
        for role in rm.roles:
            if req.args.get('add_role_' + role):
                subject = req.args.get(role)
                if subject:
                    rm.add_role(repo, role, subject)
                    return True
                add_warning(req, _("Please choose an option from the list."))
        return False

    def _get_repository_data_from_request(self, req, prefix=''):
        """Fill a dict with common repository data for create/fork/modify
        actions.
        """
        directory = req.args.get(prefix + 'dir', req.args.get(prefix + 'name'))
        return {'name': req.args.get(prefix + 'name'),
                'type': req.args.get(prefix + 'type'),
                'dir': normalize_whitespace(directory),
                'owner': req.args.get(prefix + 'owner', req.authname)}

class BrowserModule(Component):
    """Add navigation items to the browser."""

    implements(INavigationContributor, IRequestFilter)

    ### INavigationContributor methods
    def get_active_navigation_item(self, req):
        return 'browser'

    def get_navigation_items(self, req):
        if 'BROWSER_VIEW' in req.perm and 'REPOSITORY_CREATE' in req.perm:
            yield ('mainnav', 'browser',
                   tag.a(_("Browse Source"), href=req.href.browser()))

    ### IRequestFilter methods
    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        if 'BROWSER_VIEW' in req.perm and re.match(r'^/browser', req.path_info):
            rm = RepositoryManager(self.env)
            path = req.args.get('path', '/')
            reponame, repo, path = rm.get_repository_by_path(path)
            if repo:
                if path == '/':
                    try:
                        convert_managed_repository(self.env, repo)
                        if 'REPOSITORY_FORK' in req.perm and repo.is_forkable:
                            href = req.href.repository('fork', repo.reponame)
                            add_ctxtnav(req, _("Fork"), href)
                        if (repo.owner == req.authname or
                            'REPOSITORY_ADMIN' in req.perm):
                            href = req.href.repository('modify', repo.reponame)
                            add_ctxtnav(req, _("Modify"), href)
                            href = req.href.repository('remove', repo.reponame)
                            add_ctxtnav(req, _("Remove"), href)
                    except:
                        pass

                    try:
                        convert_forked_repository(self.env, repository)
                        origin = repo.origin.reponame
                        add_ctxtnav(req, _("Forked from %(origin)s",
                                           origin=origin),
                                    req.href.browser(origin))
                    except:
                        pass
            else:
                if 'REPOSITORY_CREATE' in req.perm:
                    add_ctxtnav(req, _("Create Repository"),
                                req.href.repository('create'))

        return template, data, content_type

    ### Private methods
