# This file is part of jenkins-epo
#
# jenkins-epo is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or any later version.
#
# jenkins-epo is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# jenkins-epo.  If not, see <http://www.gnu.org/licenses/>.

import collections
import copy
import logging
import pkg_resources
import re
import reprlib
import yaml

from .settings import SETTINGS
from .utils import Bunch, parse_datetime, match, parse_patterns


logger = logging.getLogger(__name__)


class Bot(object):
    DEFAULTS = {
        'errors': [],
        'poll_queue': [],
        'cancel_queue': [],
    }

    PARSE_ERROR_COMMENT = """\
%(instruction)s

Sorry %(mention)s, I don't understand what you mean:

```
%(error)s
```

See `jenkins: help` for documentation.
"""  # noqa

    instruction_re = re.compile(
        '('
        # Case beginning:  jenkins: ... or `jenkins: ...`
        '\A`*jenkins:[^\n]*`*' '|'
        # Case one middle line:  jenkins: ...
        '(?!`)\njenkins:[^\n]*' '|'
        # Case middle line teletype:  `jenkins: ...`
        '\n`+jenkins:[^\n]*`+' '|'
        # Case block code: ```\njenkins:\n  ...```
        '```(?: *ya?ml)?\njenkins:[\s\S]*?\n```'
        ')'
    )
    ext_patterns = parse_patterns(SETTINGS.EXTENSIONS)

    def __init__(self, queue_empty=True):
        self.queue_empty = queue_empty

        self.extensions_map = {}
        for ep in pkg_resources.iter_entry_points(__name__ + '.extensions'):
            cls = ep.load()
            if not match(ep.name, self.ext_patterns):
                logger.debug("Filtered extension %s.", ep.name)
                continue
            ext = cls(ep.name, self)
            if not ext.is_enabled(SETTINGS):
                logger.debug("Disabled extension %s.", ext)
                continue
            self.extensions_map[ep.name] = ext
            SETTINGS.load(ext.SETTINGS)
            logger.debug("Loaded extension %s.", ext)

        self.extensions = sorted(
            self.extensions_map.values(), key=Extension.sort_key
        )

    def workon(self, head):
        self.current = Bunch(copy.deepcopy(self.DEFAULTS))
        self.current.head = head
        self.current.repository = head.repository
        self.current.SETTINGS = head.repository.SETTINGS

        for ext in self.extensions:
            self.current.update(copy.deepcopy(ext.DEFAULTS))
            ext.current = self.current
        return self

    def run(self, head):
        self.workon(head)

        self.current.last_commit = head.last_commit

        payload = head.last_commit.fetch_statuses()
        head.last_commit.process_statuses(payload)
        self.current.statuses = head.last_commit.statuses
        for ext in self.extensions:
            ext.begin()

        self.process_instructions(self.current.head.list_comments())
        repr_ = reprlib.Repr()
        repr_.maxdict = repr_.maxlist = repr_.maxother = 64
        vars_repr = repr_.repr1(dict(self.current), 2)
        logger.debug("Bot vars: %s", vars_repr)

        for ext in self.extensions:
            ext.run()

    def parse_instructions(self, comments):
        process = True
        for comment in comments:
            if comment['body'] is None:
                continue

            body = comment['body'].replace('\r', '')
            for stanza in self.instruction_re.findall(body):
                stanza = stanza.strip().strip('`').strip()

                if stanza.startswith('yml\n'):
                    stanza = stanza[3:].strip()

                if stanza.startswith('yaml\n'):
                    stanza = stanza[4:].strip()

                date = parse_datetime(comment['updated_at'])
                author = comment['user']['login']

                try:
                    payload = yaml.load(stanza)
                except yaml.error.YAMLError as e:
                    quote = '> '.join(
                        ['', '```\n'] +
                        stanza.lstrip().splitlines(True) +
                        ['```'],
                    )
                    body = self.PARSE_ERROR_COMMENT % dict(
                        error=e, instruction=quote, mention='@' + author,
                    )
                    self.current.errors.append(Error(body, date))
                    continue

                if not isinstance(payload, dict):
                    quote = '> '.join(
                        ['', '```\n'] +
                        (stanza.strip() + '\n').splitlines(True) +
                        ['```'],
                    )
                    body = self.PARSE_ERROR_COMMENT % dict(
                        error="Instruction is not a YAML dict",
                        instruction=quote, mention='@' + author,
                    )
                    self.current.errors.append(Error(body, date))
                    continue

                data = payload.pop('jenkins')
                # If jenkins is empty, reset to dict
                data = data or {}
                # If spurious keys are passed, this may be an unindented yaml,
                # just include it.
                if payload:
                    data = dict(payload, **data)

                if not data:
                    continue
                if isinstance(data, str):
                    data = {data: None}
                if isinstance(data, list):
                    data = collections.OrderedDict(zip(data, [None]*len(data)))

                for name, args in data.items():
                    name = name.lower()
                    instruction = Instruction(author, name, args, date)
                    if not process:
                        process = 'process' == instruction
                        continue
                    elif instruction == 'ignore':
                        process = False
                    else:
                        yield instruction

    def process_instructions(self, comments):
        for instruction in self.parse_instructions(comments):
            for ext in self.extensions:
                ext.process_instruction(instruction)


class Instruction(object):
    def __init__(self, author, name, args=None, date=None):
        self.name = name
        self.args = args
        self.author = author
        self.date = date

    def __str__(self):
        return self.name

    def __repr__(self):
        return '%s(%s, %s)' % (
            self.__class__.__name__,
            self.author, self.name
        )

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, str):
            return str(self) == other
        else:
            return super(Instruction, self).__eq__(other)


class Extension(object):
    stage = '50'

    DEFAULTS = {}
    SETTINGS = {}

    def __init__(self, name, bot):
        self.name = name
        self.bot = bot

    def __repr__(self):
        return '<%s %s>' % (self.__class__.__name__, self.name)

    def __str__(self):
        return self.name

    def sort_key(self):
        return self.stage, self.name

    def is_enabled(self, settings):
        return True

    def begin(self):
        self.bot.current.update(copy.deepcopy(self.DEFAULTS))

    def process_instruction(self, instruction):
        pass

    def run(self):
        pass


class Error(object):
    def __init__(self, body, date):
        self.body = body
        self.date = date
