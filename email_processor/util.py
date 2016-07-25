#
# Copyright (c) Justus Winter <4winter@informatik.uni-hamburg.de>
#
# Permission to use, copy, modify, and distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#

# NOTE: Taken from afew.utils
import notmuch
import email
import sys
import codecs
import re
import os
import logging
import functools

logger = logging.getLogger('email_parser')
ch = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] - [%(levelname)-5s] - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)


class Pipeline(object):
    """Accepts a list of stages/sink and produces a coroutine pipeline for
    message processing."""

    def __init__(self, pipeline):
        self.pipeline = functools.reduce(
            lambda a, b: b(a), pipeline[::-1])

    def send(self, msg):
        self.pipeline.send(msg)


class MessageProxy(object):
    __slots__ = ["_msg", "__weakref__"]

    dryrun = False
    debug = False

    def __init__(self, obj):
        object.__setattr__(self, "_msg", obj)
        self._mail = None
        self._body = None

        self._add_tags = set([])
        self._remove_tags = set([])

    signature_line_re = re.compile(r'^((--)|(__)|(==)|(\*\*)|(##))')

    def _strip_signatures(self, lines, max_signature_size=10):
        r'''
        Strip signatures from a mail. Used to filter mails before
        classifying mails.

        :param lines: a mail split at newlines
        :type  lines: :class:`list` of :class:`str`
        :param max_signature_size: consider message parts up to this size as signatures
        :type  max_signature_size: int
        :returns: the mail with signatures stripped off
        :rtype:   :class:`list` of :class:`str`


        >>> strip_signatures([
        ...     'Huhu',
        ...     '--',
        ...     'Ikke',
        ... ])
        ['Huhu']
        >>> strip_signatures([
        ...     'Huhu',
        ...     '--',
        ...     'Ikke',
        ...     '**',
        ...     "Sponsored by PowerDoh\'",
        ...     "Sponsored by PowerDoh\'",
        ...     "Sponsored by PowerDoh\'",
        ...     "Sponsored by PowerDoh\'",
        ...     "Sponsored by PowerDoh\'",
        ... ], 5)
        ['Huhu']
        '''

        siglines = 0
        sigline_count = 0

        for n, line in enumerate(reversed(lines)):
            if self.signature_line_re.match(line):
                # set the last line to include
                siglines = n + 1

                # reset the line code
                sigline_count = 0

            if sigline_count >= max_signature_size:
                break

            sigline_count += 1

        return lines[:-siglines]

    @property
    def body(self):
        r'''
        Extract the plain text body of the message with signatures
        stripped off.

        :param message: the message to extract the body from
        :type  message: :class:`notmuch.Message`
        :returns: the extracted text body
        :rtype:   :class:`list` of :class:`str`o
        '''
        if self._body is None:
            content = []
            for part in self.mail.walk():
                if part.get_content_type() == 'text/plain':
                    raw_payload = part.get_payload(decode=True)
                    encoding = part.get_content_charset()
                    if encoding:
                        try:
                            raw_payload = raw_payload.decode(encoding,
                                                             'replace')
                        except LookupError:
                            raw_payload = raw_payload.decode(
                                sys.getdefaultencoding(), 'replace')
                    else:
                        raw_payload = raw_payload.decode(
                            sys.getdefaultencoding(), 'replace')

                    lines = raw_payload.split('\n')
                    lines = self._strip_signatures(lines)

                    content.append('\n'.join(lines))
            self._body = '\n'.join(content)

        return self._body

    @property
    def mail(self):
        if self._mail is None:
            if hasattr(email, 'message_from_binary_file'):
                self._mail = email.message_from_binary_file(
                    open(self._msg.get_filename(), 'br'))
            else:
                if (3, 1) <= sys.version_info < (3, 2):
                    fp = codecs.open(self._msg.get_filename(),
                                     'r', 'utf-8', errors='replace')
                else:
                    fp = open(self._msg.get_filename())

                    self._mail = email.message_from_file(fp)

        return self._mail

    def _get_keywords(self, s):
        start = s.find("X-Keywords:")
        if start == -1:
            raise AttributeError("X-Keywords header not found")

        end = s.find("\n", start)
        return start + len("X-Keywords:"), end

    def get_keywords(self):
        with open(self._msg.get_filename(), 'rb') as fh:
            m = fh.read()

        start, end = self._get_keywords(m)

        return [t for t in m[start:end].strip().split(",") if t != '']

    def set_keywords(self, keywords):
        with open(self._msg.get_filename(), 'r+b') as fh:
            m = fh.read()

            start, end = self._get_keywords(m)
            fh.seek(0)
            fh.write(m[:start] + ' ' +  b','.join(keywords) + m[end:])
            fh.truncate()

        self._mail = None

    def add_tag(self, tag, sync_maildir_flags=False):
        assert tag is not None, "tag is None!"
        assert tag is not "", "tag is empty!"

        if self.debug:
            self._add_tags = self._add_tags | (set(["+" + tag]))

        if not self.dryrun:
            return self._msg.add_tag(
                tag, sync_maildir_flags=sync_maildir_flags)

        return notmuch.STATUS.SUCCESS

    def remove_tag(self, tag, sync_maildir_flags=False):
        if self.debug:
            self._remove_tags =  self._remove_tags | set(["-" + tag])

        if not self.dryrun:
            return self._msg.remove_tag(
                tag, sync_maildir_flags=sync_maildir_flags)

        return notmuch.STATUS.SUCCESS

    # Should add get_header, get_date here, pulling from self.mail

    ###########################################################################
    # Proxying Code
    #
    def __getattribute__(self, name):
        try:
            return object.__getattribute__(self, name)
        except AttributeError:
            return getattr(object.__getattribute__(self, "_msg"), name)

    def __delattr__(self, name):
        delattr(object.__getattribute__(self, "_msg"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_msg"), name, value)

    def __nonzero__(self):
        return bool(object.__getattribute__(self, "_msg"))

    def __str__(self):
        return str(object.__getattribute__(self, "_msg"))

    def __repr__(self):
        return repr(object.__getattribute__(self, "_msg"))

    #
    # factories
    #
    _special_names = [
        '__abs__', '__add__', '__and__', '__call__', '__cmp__', '__coerce__',
        '__contains__', '__delitem__', '__delslice__', '__div__', '__divmod__',
        '__eq__', '__float__', '__floordiv__', '__ge__', '__getitem__',
        '__getslice__', '__gt__', '__hash__', '__hex__', '__iadd__', '__iand__',
        '__idiv__', '__idivmod__', '__ifloordiv__', '__ilshift__', '__imod__',
        '__imul__', '__int__', '__invert__', '__ior__', '__ipow__', '__irshift__',
        '__isub__', '__iter__', '__itruediv__', '__ixor__', '__le__', '__len__',
        '__long__', '__lshift__', '__lt__', '__mod__', '__mul__', '__ne__',
        '__neg__', '__oct__', '__or__', '__pos__', '__pow__', '__radd__',
        '__rand__', '__rdiv__', '__rdivmod__', '__reduce__', '__reduce_ex__',
        '__repr__', '__reversed__', '__rfloorfiv__', '__rlshift__', '__rmod__',
        '__rmul__', '__ror__', '__rpow__', '__rrshift__', '__rshift__', '__rsub__',
        '__rtruediv__', '__rxor__', '__setitem__', '__setslice__', '__sub__',
        '__truediv__', '__xor__', 'next',
    ]

    @classmethod
    def _create_class_proxy(cls, theclass):
        """creates a proxy for the given class"""

        def make_method(name):
            def method(self, *args, **kw):
                return getattr(object.__getattribute__(self, "_msg"), name)(*args, **kw)
            return method

        namespace = {}
        for name in cls._special_names:
            if hasattr(theclass, name):
                namespace[name] = make_method(name)
        return type("%s(%s)" % (cls.__name__, theclass.__name__), (cls,), namespace)

    def __new__(cls, obj, *args, **kwargs):
        """
        creates an proxy instance referencing `obj`. (obj, *args, **kwargs) are
        passed to this class' __init__, so deriving classes can define an
        __init__ method of their own.
        note: _class_proxy_cache is unique per deriving class (each deriving
        class must hold its own cache)
        """
        try:
            cache = cls.__dict__["_class_proxy_cache"]
        except KeyError:
            cls._class_proxy_cache = cache = {}
        try:
            theclass = cache[obj.__class__]
        except KeyError:
            cache[obj.__class__] = theclass = cls._create_class_proxy(obj.__class__)
        ins = object.__new__(theclass)
        theclass.__init__(ins, obj, *args, **kwargs)
        return ins
