import notmuch
from notmuch import Database, Query
from util import MessageProxy, logger, Pipeline
import sys
import logging
import os
import functools


DATABASE_PATH = "/home/kotfic/mail_test"
GMAIL_HEADERS_TO_TAGS = {
    '\\Important': 'important',
    '\\Starred': 'flagged',
    '\\Sent': 'sent',
    '\\Inbox': 'inbox'}

GMAIL_TAGS_TO_HEADERS = {v: k for k, v in GMAIL_HEADERS_TO_TAGS.items()}

maildir_tags = set([
    'unread',
    'draft',
    'flagged',
    'passed',
    'signed',
    'replied'])


MessageProxy.debug = True
MessageProxy.dryrun = True

def db():
    return Database(DATABASE_PATH, create=False, mode=Database.MODE.READ_WRITE)


def count_messages(query_string):
    return Query(db(), query_string).count_messages()


def get_messages(query_string):
    for msg in Query(db(), query_string).search_messages():
        yield MessageProxy(msg)


def toggle_header(item):
    try:
        return GMAIL_HEADERS_TO_TAGS[item]
    except KeyError:
        pass
    try:
        return GMAIL_TAGS_TO_HEADERS[item]
    except KeyError:
        pass

    return item


def coroutine(func):
    def _coroutine(*args, **kwargs):
        cr = func(*args, **kwargs)
        cr.next()
        return cr

    return _coroutine

def stage(func):
    def _stage(target, *args, **kwargs):
        try:
            while True:
                message = (yield)
                message = func(message, *args, **kwargs)
                target.send(message)
        except GeneratorExit:
            pass
    return coroutine(_stage)

def sink(func):
    def _sink(*args, **kwargs):
        try:
            while True:
                message = (yield)
                func(message, *args, **kwargs)
        except GeneratorExit:
            pass

    # Syncs should always be primed
    return coroutine(_sink)()


@stage
def sync_gmail_tags(message):

    tags = set(str(t) for t in msg.get_tags() if t not in maildir_tags)
    try:
        keywords = set(toggle_header(t) for t in msg.get_keywords())
    except AttributeError:
        return message

    for tag in (tags - keywords):
        message.remove_tag(tag)

    for tag in (keywords - tags):
        message.add_tag(tag)

    return message



@stage
def remove_new(message):
    message.remove_tag("new")
    return message

@sink
def log_output(message):
    if MessageProxy.debug:
        logger.info(log_format.format(
            truncate(msg.mail['From'], fw),
            truncate(msg.mail['Subject'], sw),
            str(message._msg.get_tags()),
            ", ".join(msg._add_tags | msg._remove_tags)))


def truncate(s, w):
    if s is None or w <= 4:
        return ''

    s = ' '.join(str(s).split())
    return s if len(s) < w else s[:w - 3] + "..."

if __name__ == "__main__":
    try:
        query = sys.argv[1]
    except IndexError:
        query = 'path:"**"'

    # DRYRUN/DEBUG logging related
    if MessageProxy.debug:
        logger.setLevel(logging.INFO)
        # 48 characters for our leading format info
        # Give 60% of screen to message
        _, COLUMNS = os.popen('stty size', 'r').read().split()

        fw = int((int(COLUMNS) - 50)  * 0.15)
        sw = int((int(COLUMNS) - 50)  * 0.55)
        ptw = int((int(COLUMNS) - 50) * 0.1)
        log_format = "{0: <" + str(fw) + "} :: {1: <" + str(sw) + "} :: {2: <" + str(ptw) + "} :: {3}"

    logger.debug("Query: {}".format(query))

    pipeline = Pipeline([sync_gmail_tags, remove_new, log_output])

    try:
        for msg in get_messages(query):
            msg.freeze()

            pipeline.send(msg)

            msg.thaw()
    except notmuch.errors.NullPointerError:
        logger.error("Query returned no results")
        pipeline.close()
