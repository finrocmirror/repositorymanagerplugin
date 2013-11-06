from ..api import *

from trac.core import implements, Component
from trac.ticket.api import TicketSystem, ITicketActionController
from trac.ticket.model import Resolution
from trac.util.translation import _, tag_
from trac.config import OrderedExtensionsOption

from genshi.builder import tag

from itertools import chain

class PullRequestWorkflowProxy(Component):
    """Provides a special workflow for pull requests and forwards others.

    Don't forget to replace the `TicketActionController` in the workflow
    option in the `[ticket]` section in TracIni. Your original
    controller for tickets other than pull requests can be added as
    general_workflow option.
    If there was only the default workflow option before, the lines will
    look like this:
    {{{
    [ticket]
    workflow = PullRequestWorkflowProxy
    general_workflow = ConfigurableTicketWorkflow
    }}}
    """

    implements(ITicketActionController)

    action_controllers = OrderedExtensionsOption('ticket', 'general_workflow',
        ITicketActionController, default='ConfigurableTicketWorkflow',
        include_missing=False,
        doc="""Ordered list of workflow controllers to use for general tickets.
               That is when a ticket is not a pull request.
               """)

    ### ITicketActionController methods
    def get_ticket_actions(self, req, ticket):
        if ticket['type'] != 'pull request':
            items = (controller.get_ticket_actions(req, ticket)
                     for controller in self.action_controllers)
            return chain.from_iterable(items)

        rm = RepositoryManager(self.env)
        repo = rm.get_repository_by_id(ticket['pr_dstrepo'], True)
        srcrepo = rm.get_repository_by_id(ticket['pr_srcrepo'], True)

        current_status = ticket._old.get('status', ticket['status']) or 'new'
        current_owner = ticket._old.get('owner', ticket['owner'])

        actions = []
        actions.append((4, 'leave'))
        if req.authname in repo.maintainers() and current_status != 'closed':
            actions.append((3, 'accept'))
            actions.append((2, 'reject'))
            if not current_owner or repo.maintainers() - set([current_owner]):
                actions.append((1, 'reassign'))
            actions.append((0, 'review'))
        if current_status == 'closed':
            if srcrepo and repo:
                actions.append((0, 'reopen'))
        return actions

    def get_all_status(self):
        items = (controller.get_all_status()
                     for controller in self.action_controllers)
        return list(chain.from_iterable(items)) + ['under review']

    def render_ticket_action_control(self, req, ticket, action):
        if ticket['type'] != 'pull request':
            items = [controller.render_ticket_action_control(req, ticket,
                                                             action)
                     for controller in self.action_controllers]
            return chain.from_iterable(self._filter_resolutions(req, items))

        rm = RepositoryManager(self.env)
        repo = rm.get_repository_by_id(ticket['pr_dstrepo'], True)

        current_status = ticket._old.get('status', ticket['status']) or 'new'
        current_owner = ticket._old.get('owner', ticket['owner'])

        control = []
        hints = []
        if action == 'leave':
            control.append(_('as %(status)s ', status=current_status))
            if current_owner:
                hints.append(_("The owner will remain %(current_owner)s",
                               current_owner=current_owner))
            else:
                hints.append(_("The ticket will remain with no owner",
                               owner=current_owner))
        if action == 'accept':
            if repo.has_node('', ticket['pr_srcrev']):
                hints.append(_("The request will be accepted"))
                hints.append(_("Next status will be '%(name)s'", name='closed'))
            else:
                hints.append(_("The changes must be merged into '%(repo)s' "
                               "first", repo=repo.reponame))
        if action == 'reject':
            if not repo.has_node('', ticket['pr_srcrev']):
                hints.append(_("The request will be rejected"))
                hints.append(_("Next status will be '%(name)s'", name='closed'))
            else:
                hints.append(_("The changes are already present in '%(repo)s'",
                               repo=repo.reponame))
        if action == 'reassign':
            maintainers = (set([repo.owner]) | repo.maintainers())
            maintainers -= set([current_owner])
            selected_owner = req.args.get('action_reassign_reassign_owner',
                                          req.authname)
            control.append(tag.select([tag.option(x, value=x,
                                                  selected=(x == selected_owner
                                                            or None))
                                       for x in maintainers],
                                      id='action_reassign_reassign_owner',
                                      name='action_reassign_reassign_owner'))
            hints.append(_("The owner will be changed from %(old)s to the "
                           "selected user. Next status will be 'assigned'",
                           old=current_owner))
        if action == 'review':
            if current_owner != req.authname:
                hints.append(_("The owner will be changes from "
                               "%(current_owner)s to %(authname)s",
                               current_owner=current_owner,
                               authname=req.authname))
            hints.append(_("Next status will be '%(name)s'",
                           name='under review'))
        if action == 'reopen':
             hints.append(_("The resolution will be deleted"))
             hints.append(_("Next status will be '%(name)s'", name='reopened'))

        return (action, tag(control), '. '.join(hints) + '.')

    def get_ticket_changes(self, req, ticket, action):
        changes = {}
        if ticket['type'] != 'pull request':
            for controller in self.action_controllers:
                changes.update(controller.get_ticket_changes(req, ticket,
                                                             action))
            return changes
        updated = {}
        if action == 'accept':
            updated['resolution'] = 'accepted'
            updated['status'] = 'closed'
        if action == 'reject':
            updated['resolution'] = 'rejected'
            updated['status'] = 'closed'
        if action == 'reassign':
            updated['owner'] = req.args.get('action_reassign_reassign_owner')
            updated['status'] = 'assigned'
        if action == 'review':
            updated['owner'] = req.authname
            updated['status'] = 'under review'
        if action == 'reopen':
            updated['resolution'] = ''
            updated['status'] = 'reopened'
        return updated

    def apply_action_side_effects(self, req, ticket, action):
        if ticket['type'] != 'pull request':
            items = (controller.get_action_side_effects(req, ticket, action)
                     for controller in self.action_controllers)
            return chain.from_iterable(items)

    ### Private methods
    def _filter_resolutions(self, req, items):
        for item in items:
            if item[0] != 'resolve':
                yield item
                return

            resolutions = [val.name for val in Resolution.select(self.env)
                           if int(val.value) > 0]
            ts = TicketSystem(self.env)
            selected_option = req.args.get('action_resolve_resolve_resolution',
                                           ts.default_resolution)
            control = tag.select([tag.option(x, value=x,
                                             selected=(x == selected_option
                                                       or None))
                                  for x in resolutions],
                                 id='action_resolve_resolve_resolution',
                                 name='action_resolve_resolve_resolution')

            yield ('resolve', tag_('as %(resolution)s', resolution=control),
                   item[2])
