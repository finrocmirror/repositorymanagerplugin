from ..api import *

from trac.core import *
from trac.web import IRequestHandler, IRequestFilter
from trac.ticket.web_ui import TicketModule
from trac.util.translation import _

import os
import re

class PullrequestModule(Component):
    implements(IRequestHandler, IRequestFilter)

    ### IRequestFilter methods
    def pre_process_request(self, req, handler):
        return handler

    def post_process_request(self, req, template, data, content_type):
        if data and data.get('ticket'):
            if data.get('ticket')['type'] == 'pull request':
                return 'pullrequest.html', data, content_type

        if req.path_info == '/newpullrequest':
            return 'pullrequest.html', data, content_type

        return template, data, content_type

    ### IRequestHandler methods
    def match_request(self, req):
        if req.path_info == '/newpullrequest':
            return True

    def process_request(self, req):
        return TicketModule(self.env).process_request(req)

    ### Private methods
