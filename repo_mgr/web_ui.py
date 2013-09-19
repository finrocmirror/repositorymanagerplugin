from api import *

from trac.core import *
from trac.perm import IPermissionRequestor, PermissionError
from trac.web import IRequestHandler, IRequestFilter
from trac.web.auth import LoginModule
from trac.web.chrome import INavigationContributor, ITemplateProvider, \
                            add_ctxtnav, add_stylesheet, \
                            add_notice, add_warning
from trac.versioncontrol.admin import RepositoryAdminPanel
from trac.util import is_path_below
from trac.util.translation import _, tag_
from trac.util.text import normalize_whitespace
from trac.config import BoolOption, PathOption

from genshi.builder import tag

import os
import re

class RepositoryManagerModule(Component):
    implements(IPermissionRequestor, IRequestHandler, ITemplateProvider)

    base_dir = PathOption('repository-manager', 'base_dir', 'repositories',
                          doc="""The base folder in which repositories will be
                                 created
                                 """)

    ### IPermissionRequestor methods
    def get_permission_actions(self):
        return ['REPOSITORY_CREATE', 'REPOSITORY_FORK', 'REPOSITORY_MODIFY',
                'REPOSITORY_REMOVE']

    ### IRequestHandler methods
    def match_request(self, req):
        match = re.match(r'^/repository(/(\w+))?(/(\w+))?', req.path_info)
        if match:
            _, action, _, reponame = match.groups()
            req.args['action'] = action or 'list'
            req.args['reponame'] = reponame or None
            return True

    def process_request(self, req):
        action = req.args.get('action', 'list')
        if action == 'list':
            req.redirect(req.href.browser())

        data = {'action': action}

        if action == 'create':
            self._process_create_request(req, data)
        elif action == 'modify':
            self._process_modify_request(req, data)
        elif action == 'remove':
            self._process_remove_request(req, data)

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
    def _get_base_directory(self, type):
        return os.path.join(self.env.path, self.base_dir, type)

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

    def _create(self, req, repo, creator):
        if not repo['name']:
            add_warning(req, _("Missing arguments to create a repository."))
        elif self._check_and_update_repository(req, repo):
            creator(repo)
            link = tag.a(repo['name'], href=req.href.browser(repo['name']))
            add_notice(req, tag_('The repository "%(link)s" has been created.',
                                 link=link))
            req.redirect(req.href.repository('create'))

    def _process_create_request(self, req, data):
        req.perm.require('REPOSITORY_CREATE')
        repository, local_fork, remote_fork = {}, {}, {}

        rm = RepositoryManager(self.env);

        if req.args.get('create'):
            directory = req.args.get('dir', req.args.get('name'))
            repository = {'name': req.args.get('name'),
                          'type': req.args.get('type'),
                          'dir': normalize_whitespace(directory),
                          'owner': req.authname}
            self._create(req, repository, rm.create)

        elif req.args.get('fork_local'):
            directory = req.args.get('dir', req.args.get('name'))
            local_fork = {'name': req.args.get('name'),
                          'dir': normalize_whitespace(directory),
                          'owner': req.authname,
                          'origin': req.args.get('origin')}
            origin = rm.get_repository(local_fork['origin'], True)
            if not origin:
                add_warning(req, "Origin does not exist.")
            else:
                local_fork.update({'type': origin.type})
                self._create(req, local_fork, rm.fork_local)

        elif req.args.get('fork_remote'):
            directory = req.args.get('dir', req.args.get('name'))
            remote_fork = {'name': req.args.get('name'),
                           'type': req.args.get('type'),
                           'dir': normalize_whitespace(directory),
                           'owner': req.authname}
            self._create(req, remote_fork, rm.fork_remote)

        data.update({'title': _("Create Repository"),
                     'supported_repository_types': rm.get_supported_types(),
                     'forkable_repository_types': rm.get_forkable_types(),
                     'forkable_repositories': rm.get_forkable_repositories(),
                     'repository': repository,
                     'local_fork': local_fork,
                     'remote_fork': remote_fork})

    def _check_repository(self, req, name, permission):
        if not name:
            raise TracError(_("Repository not specified"))

        rm = RepositoryManager(self.env)
        repository = rm.get_repository(name, True)
        if not repository:
            raise TracError(_('Repository "%(name)s" does not exist.',
                              name=name))

        if not (permission in req.perm and req.authname == repository.owner):
            raise PermissionError(permission, None, self.env)

        return repository

    def _process_modify_request(self, req, data):
        repo = self._check_repository(req, req.args.get('reponame'),
                                      'REPOSITORY_MODIFY')

        base_directory = self._get_base_directory(repo.type)
        prefix_length = len(base_directory)
        if prefix_length > 0:
            prefix_length += 1

        directory = req.args.get('directory', repo.directory[prefix_length:])
        new = {'name': req.args.get('name', repo.reponame),
               'type': repo.type,
               'dir': normalize_whitespace(directory)}

        if req.args.get('modify'):
            if self._check_and_update_repository(req, new):
                RepositoryManager(self.env).modify(repo, new)
                link = tag.a(repo.reponame, href=req.href.browser(new['name']))
                add_notice(req, tag_('The repository "%(link)s" has been '
                                     'modified.', link=link))
                req.redirect(req.href.repository())
        elif req.args.get('cancel'):
            LoginModule(self.env)._redirect_back(req)

        referer = req.args.get('referer', req.get_header('Referer'))
        data.update({'title': _("Modify Repository"),
                     'repository': repo,
                     'new': new,
                     'referer': referer})

    def _process_remove_request(self, req, data):
        repo = self._check_repository(req, req.args.get('reponame'),
                                      'REPOSITORY_REMOVE')

        if req.args.get('confirm'):
            RepositoryManager(self.env).remove(repo, req.args.get('delete'))
            add_notice(req, _('The repository "%(name)s" has been removed.',
                              name=repo.reponame))
            req.redirect(req.href.repository())
        elif req.args.get('cancel'):
            LoginModule(self.env)._redirect_back(req)

        referer = req.args.get('referer', req.get_header('Referer'))
        data.update({'title': _("Remove Repository"),
                     'repository': repo,
                     'referer': referer})

class BrowserModule(Component):
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
#                        if 'REPOSITORY_FORK' in req.perm and repos.is_forkable:
#                            add_ctxtnav(req, _("Fork"), req.href.repository('fork', repos.reponame))
                        if 'REPOSITORY_MODIFY' in req.perm:
                            href = req.href.repository('modify', repo.reponame)
                            add_ctxtnav(req, _("Modify"), href)
                        if ('REPOSITORY_REMOVE' in req.perm
                               and repo.owner == req.authname):
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
