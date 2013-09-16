from api import *

from trac.core import *
from trac.perm import IPermissionRequestor, PermissionError
from trac.web import IRequestHandler, IRequestFilter
from trac.web.auth import LoginModule
from trac.web.chrome import INavigationContributor, ITemplateProvider, add_ctxtnav, add_stylesheet, add_notice, add_warning
from trac.versioncontrol.api import RepositoryManager as TracRepositoryManager
from trac.util import is_path_below
from trac.util.translation import _, tag_
from trac.util.text import normalize_whitespace
from trac.config import BoolOption, PathOption

from genshi.builder import tag

import os
import re

class RepositoryManagerModule(Component):
    implements(IPermissionRequestor, IRequestHandler, ITemplateProvider)

    base_dir = PathOption('repository-manager', 'base_dir', 'repositories', doc="""The base folder in which repositories will be created""")
    use_type_subdirs = BoolOption('repository-manager', 'use_type_subdirs', True, doc="""Use a subfolder for each repository type""")

    ### IPermissionRequestor methods
    def get_permission_actions(self):
        return ['REPOSITORY_CREATE', 'REPOSITORY_FORK', 'REPOSITORY_MODIFY', 'REPOSITORY_REMOVE']

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
            self.process_create_request(req, data)
        elif action == 'remove':
            self.process_remove_request(req, data)

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
    def check_and_update_repository(self, req, repository):
        """Check if a repository is valid, does not already exist, update the dict and add a warning message otherwise."""
        if not repository['directory']:
            add_warning(req, _('The directory is missing.'))
            return False

        base = os.path.join(self.env.path, self.base_dir)
        if self.use_type_subdirs:
            base = os.path.join(base, repository['type'])

        directory = os.path.join(base, repository['directory'])

        if os.path.lexists(directory):
            add_warning(req, _('Directory "%(name)s" already exists',
                               name=directory))
            return False

        prefixes = [os.path.join(self.env.path, prefix)
                    for prefix in RepositoryAdminPanel(self.env).allowed_repository_dir_prefixes]
        if prefixes and not any(is_path_below(directory, prefix)
                                for prefix in prefixes):
            add_warning(req, _('The repository directory must be located '
                               'below one of the following directories: '
                               '%(dirs)s', dirs=', '.join(prefixes)))
            return False

        if TracRepositoryManager(self.env).get_repository(repository['name']):
            add_warning(req, _('Repository "%(name)s" already exists',
                               name=repository['name']))
            return False

        repository.update({'directory': directory})
        return True

    def create(self, req, repository, creator):
        if not repository['name']:
            add_warning(req, _('Missing arguments to create a repository.'))
        elif self.check_and_update_repository(req, repository):
            creator(repository)
            link = tag.a(repository['name'], href=req.href.browser(repository['name']))
            add_notice(req, tag_('The repository "%(link)s" has been created.', link=link))
            req.redirect(req.href.repository('create'))

    def process_create_request(self, req, data):
        req.perm.require('REPOSITORY_CREATE')
        repository, local_fork, remote_fork = {}, {}, {}

        rm = RepositoryManager(self.env);

        if req.args.get('create'):
            repository = {'name': req.args.get('name'),
                          'type': req.args.get('type'),
                          'directory': normalize_whitespace(req.args.get('directory', req.args.get('name'))),
                          'owner': req.authname}
            self.create(req, repository, rm.create)

        if req.args.get('fork_local'):
            local_fork = {'name': req.args.get('name'),
                          'directory': normalize_whitespace(req.args.get('directory', req.args.get('name'))),
                          'owner': req.authname,
                          'origin': req.args.get('origin')}
            origin = TracRepositoryManager(self.env).get_all_repositories().get(local_fork['origin'])
            if not origin:
                add_warning(req, 'Origin does not exist.')
            else:
                local_fork.update({'type': origin['type']})
                self.create(req, local_fork, rm.fork_local)

        if req.args.get('fork_remote'):
            remote_fork = {'name': req.args.get('name'),
                           'type': req.args.get('type'),
                           'directory': normalize_whitespace(req.args.get('directory', req.args.get('name'))),
                           'owner': req.authname}
            self.create(req, remote_fork, rm.fork_remote)

        forkable_types = rm.get_forkable_types()
        repositories = TracRepositoryManager(self.env).get_all_repositories()
        forkable_repositories = (repositories[repo]['name'] for repo in repositories
                                 if repositories[repo]['type'] in forkable_types)

        data.update({'title': 'Create Repository',
                     'supported_repository_types': rm.get_supported_types(),
                     'forkable_repository_types': forkable_types,
                     'forkable_repositories': forkable_repositories,
                     'repository': repository,
                     'local_fork': local_fork,
                     'remote_fork': remote_fork})

    def process_remove_request(self, req, data):
        reponame = req.args.get('reponame')
        if not reponame:
            raise TracError(_('Repository not specified'))
        repository = TracRepositoryManager(self.env).get_repository(reponame)
        convert_managed_repository(self.env, repository)
        if not ('REPOSITORY_REMOVE' in req.perm and req.authname == repository.owner):
            raise PermissionError('REPOSITORY_REMOVE', None, self.env)
        if req.args.get('confirm'):
            RepositoryManager(self.env).remove(repository, req.args.get('delete'))
            add_notice(req, _('The repository %(name)s has been removed.', name=reponame))
            req.redirect(req.href.repository())
        if req.args.get('cancel'):
            LoginModule(self.env)._redirect_back(req)

        data.update({'title': 'Remove Repository',
                     'repository': repository,
                     'referer': req.args.get('referer', req.get_header('Referer'))})

class BrowserModule(Component):
    implements(INavigationContributor, IRequestFilter)

    ### INavigationContributor methods
    def get_active_navigation_item(self, req):
        return 'browser'

    def get_navigation_items(self, req):
        if 'BROWSER_VIEW' in req.perm and 'REPOSITORY_CREATE' in req.perm:
            yield ('mainnav', 'browser',
                   tag.a(_('Browse Source'), href=req.href.browser()))

    ### IRequestFilter methods
    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        if 'BROWSER_VIEW' in req.perm and re.match(r'^/browser', req.path_info):
            trac_rm = TracRepositoryManager(self.env)
            reponame, repos, path = trac_rm.get_repository_by_path(req.args.get('path', '/'))
            if repos:
                if path == '/':
                    try:
                        convert_managed_repository(self.env, repos)
#                        if 'REPOSITORY_FORK' in req.perm and repos.is_forkable:
#                            add_ctxtnav(req, _('Fork'), req.href.repository('fork', repos.reponame))
                        if 'REPOSITORY_MODIFY' in req.perm:
                            add_ctxtnav(req, _('Modify'), req.href.repository('modify', repos.reponame))
                        if 'REPOSITORY_REMOVE' in req.perm and repos.owner == req.authname:
                            add_ctxtnav(req, _('Remove'), req.href.repository('remove', repos.reponame))
                    except:
                        pass

                    try:
                        convert_forked_repository(self.env, repos)
                        add_ctxtnav(req, _('Forked from %(origin)s', origin=repos.origin.reponame), req.href.browser(repos.origin.reponame))
                    except:
                        pass
            else:
                if 'REPOSITORY_CREATE' in req.perm:
                    add_ctxtnav(req, _('Create Repository'), req.href.repository('create'))

        return template, data, content_type

    ### Private methods
