from ..api import *

from trac.core import *
from trac.perm import IPermissionRequestor
from trac.web import IRequestHandler, IRequestFilter
from trac.web.chrome import ITemplateProvider, add_ctxtnav, add_notice, \
                            add_warning, add_script, add_stylesheet
from trac.versioncontrol.web_ui import ChangesetModule
from trac.versioncontrol.diff import get_diff_options
from trac.resource import ResourceNotFound
from trac.ticket.web_ui import TicketModule
from trac.ticket.model import Type
from trac.util.translation import _
from trac.config import Option

import os
import re

class PullrequestModule(Component):
    """Provide special ticket type: pull request

    This module mainly acts as a filter for tickets, adding and handling
    a special ticket type: pull requests.

    A pull request is a suggestion that the maintainer of a repository
    pulls changesets from a fork. It therefore implements a review
    process, where new content is not automatically flooded via the main
    repository but must be provided in a fork where it can be reviewed.

    To do so, a new ticket type is added along with some specialized
    custom fields. This module hooks into the creation, modification and
    display of tickets and handles this new type and its custom-fields.
    It also adds an according diff to these tickets and a customized
    workflow. 
    """

    implements(IPermissionRequestor, IRequestHandler, IRequestFilter)

    cf_srcrepo = Option('ticket-custom', 'pr_srcrepo', 'text')
    cf_srcrepo = Option('ticket-custom', 'pr_srcrepo.label',
                        'Source Repository')
    cf_srcrev = Option('ticket-custom', 'pr_srcrev', 'text')
    cf_srcrev = Option('ticket-custom', 'pr_srcrev.label',
                       'Source Revision')
    cf_dstrepo = Option('ticket-custom', 'pr_dstrepo', 'text')
    cf_dstrepo = Option('ticket-custom', 'pr_dstrepo.label',
                        'Destination Repository')
    cf_dstrev = Option('ticket-custom', 'pr_dstrev', 'text')
    cf_dstrev = Option('ticket-custom', 'pr_dstrev.label',
                       'Destination Revision')

    def __init__(self):
        """Setup the special ticket type.

        Checks if the Type enum contains 'pull request' and otherwise
        adds it with priority -1. That should somehow mark it *special*
        when looked at in the admin panel.
        """
        try:
            type = Type(self.env, 'pull request')
        except ResourceNotFound:
            type = Type(self.env)
            type.name = 'pull request'
            type.value = -1
            type.insert()

    ### IPermissionRequestor methods
    def get_permission_actions(self):
        return ['PULLREQUEST_CREATE']

    ### IRequestFilter methods
    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        """Here is the magic of treating pull request tickets different.

        When data contains a ticket after processing a request, first
        the internally used fields are removed to be hidden in the web
        interface. Afterwards for pull requests, the following steps are
        taken:

        1. Replace the ticket type options
        2. Check if we got a forked repository along with the ticket
        3. Build a list of possible newer revisions
        3. Render the according diff
        4. Replace some templates by own ones, that have small adaptions

        For normal tickets just remove 'pull request' from the options
        for the type field. 
        """
        if data and 'ticket' in data:

            if not 'fields' in data:
                return template, data, content_type

            ticket = data['ticket']

            data['fields'] = list(field for field in data['fields']
                                  if not re.match(r'^pr_', field['name']))

            if ticket['type'] == 'pull request':
                data['fields'] = list(self._filter_ticket_types(data['fields'],
                                                                True))

                repository = data.get('pr_srcrepo')
                if not repository:
                    rm = RepositoryManager(self.env)
                    repository = rm.get_repository(ticket['pr_srcrepo'])
                    convert_forked_repository(self.env, repository)

                srcrev = ticket['pr_srcrev']
                srcrev_list = []
                candidate = srcrev
                while candidate is not None:
                    srcrev_list.append(candidate)
                    candidate = repository.next_rev(candidate)

                dstrev = repository.get_youngest_common_ancestor(srcrev)
                data.update({'pr_srcrepo': repository,
                             'pr_srcrev': srcrev,
                             'pr_srcrev_list': srcrev_list,
                             'pr_dstrepo': repository.origin,
                             'pr_dstrev': dstrev})

                self._render_diff_html(req, data)
                add_script(req, 'common/js/diff.js')
                add_stylesheet(req, 'common/css/diff.css')
                add_stylesheet(req, 'common/css/code.css')

                if template in ['ticket.html',
                                'ticket_box.html',
                                'ticket_preview.html']:
                    template = template.replace('ticket', 'pullrequest', 1)

            else:
                data['fields'] = list(self._filter_ticket_types(data['fields'],
                                                                False))

        return template, data, content_type

    ### IRequestHandler methods
    def match_request(self, req):
        """Creating new pull requests needs a dedicated handler."""
        match = re.match(r'^/newpullrequest(/.+)$', req.path_info)
        if match:
            req.args['path'] = match.group(1)
            return True

    def process_request(self, req):
        """Creating a new pull request ticket needs some pre- and post-
        processing:

         * Check if the given repository is a fork of another known
           repository.
         * Initialize some ticket fields.

        Then forward the processing to `TicketModule`

         * Add the repository as is must be looked up via the used path
           and is not yet known to the ticket.
        """
        req.perm.require('PULLREQUEST_CREATE')

        rm = RepositoryManager(self.env)
        reponame, repo, path = rm.get_repository_by_path(req.args.get('path'))
        convert_forked_repository(self.env, repo)

        req.args['type'] = 'pull request'
        req.args['pr_srcrev'] = req.args.get('pr_srcrev',
                                             repo.get_youngest_rev())

        tm = TicketModule(self.env)
        template, data, content_type = tm.process_request(req)

        data.update({'pr_srcrepo': repo})

        return template, data, content_type

    ### ITemplateProvider methods
    def get_templates_dirs(self):
        from pkg_resources import resource_filename
        return [resource_filename(__name__, 'templates')]

    def get_htdocs_dirs(self):
        from pkg_resources import resource_filename
        return [('hw', resource_filename(__name__, 'htdocs'))]

    ### Private methods
    def _filter_ticket_types(self, fields, only_pull_request):
        """Remove 'pull request' from the types or make it the only option."""
        for field in fields:
            if field['name'] == 'type':
                if only_pull_request:
                    field.update({'value': 'pull request',
                                  'options': ['pull request']})
                else:
                    field['options'] = [option for option in field['options']
                                        if option != 'pull request']
            yield field

    def _render_diff_html(self, req, data):
        """Use Trac's rendering to show the changes in the pull request.
        
        To exploit Trac's built-in HTML rendering for diffs we must
        setup a fresh data dict and call the internal method
        `_render_html` of the `ChangesetModule`.

        Afterwards the `diff_data` must be added to the original data
        without replacing existing keys. An exception helps to find
        those keys that must be renamed. Remember to adapt the Genshi
        templates accordingly.

        XHR must be disabled even for automatic preview as Trac's
        rendering otherwise aborts request processing and immediately
        sends a result to the browser.
        """
        style, options, diff = get_diff_options(req)

        cm = ChangesetModule(self.env)
        diff_data = {}
        diff_data.update({'old_path': '',
                          'old_rev': data['pr_dstrev'],
                          'new_path': '',
                          'new_rev': data['pr_srcrev'],
                          'repos': data['pr_srcrepo'],
                          'reponame': data['pr_srcrepo'].reponame,
                          'diff': diff,
                          'wiki_format_messages': cm.wiki_format_messages})

        cm._render_html(req, data['pr_srcrepo'], False, True, False, diff_data)

        for key in diff_data:
            diff_key = key
            if key in ['changes']:
                diff_key = 'diff_' + key
            if diff_key in data:
                raise TracError("Key %(key)s collides in data", diff_key)
            data[diff_key] = diff_data[key]

class BrowserModule(Component):
    """Add navigation items to the browser."""

    implements(IRequestFilter)

    ### IRequestFilter methods
    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        if 'BROWSER_VIEW' in req.perm and re.match(r'^/browser', req.path_info):
            rm = RepositoryManager(self.env)
            path = req.args.get('path', '/')
            reponame, repos, path = rm.get_repository_by_path(path)
            if repos:
                try:
                    convert_forked_repository(self.env, repos)
                    if 'PULLREQUEST_CREATE' in req.perm:
                        rev = req.args.get('rev')
                        href = req.href.newpullrequest(reponame, pr_srcrev=rev)
                        add_ctxtnav(req, _("Open Pull Request"), href)
                except:
                    pass
        return template, data, content_type

    ### Private methods