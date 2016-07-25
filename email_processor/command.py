import notmuch
from notmuch import Database, Query
from util import MessageProxy, logger, Pipeline
import sys
import logging
import os
import subprocess
import functools
import click


DATABASE_PATH = "/home/kotfic/mail"
GMAIL_HEADERS_TO_TAGS = {
    '\\Important': 'important',
    '\\Starred': 'flagged',
    '\\Sent': 'sent',
    '\\Draft': 'draft',
    '\\Inbox': 'inbox'}

GMAIL_TAGS_TO_HEADERS = {v: k for k, v in GMAIL_HEADERS_TO_TAGS.items()}

maildir_tags = set([
    'unread',
    'draft',
    'flagged',
    'passed',
    'signed',
    'replied'])

exclude_sync_tags = set([
    'new'
]) | maildir_tags


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

    tags = set(str(t) for t in message.get_tags()
               if t not in exclude_sync_tags)
    try:
        keywords = set(toggle_header(t) for t in message.get_keywords())
    except AttributeError:
        return message

    for tag in (tags - keywords):
        message.remove_tag(tag)

    for tag in (keywords - tags):
        message.add_tag(tag)

    return message

@stage
def sync_gmail_keywords(message):
    try:
        keywords = set(toggle_header(t) for t in message.get_keywords())
    except AttributeError:
        return message

    tags = set(str(t) for t in message.get_tags()
               if t not in exclude_sync_tags)

    if not tags ^ keywords:
        return message

    if MessageProxy.debug:
        logger.info("{} :: {} :: {} :: {}".format(
            message.mail['From'], message.mail['Subject'],
            ','.join(keywords), ','.join(tags)))

    if not MessageProxy.dryrun:
        message.set_keywords([toggle_header(t) for t in list(tags)])

    return message

@stage
def remove_new(message):
    message.remove_tag("new")
    return message

@stage
def add_mention(message):
    if '@kotfic' in message.body:
        message.add_tag("mention")
    return message

@sink
def drop(message):
    pass


def log_output():
    if MessageProxy.debug:
        logger.setLevel(logging.INFO)
        # 48 characters for our leading format info
        # Give 60% of screen to message
        try:
            _, COLUMNS = subprocess.check_output(['stty', 'size']).split()

            fw = int((int(COLUMNS) - 50)  * 0.15)
            sw = int((int(COLUMNS) - 50)  * 0.55)
            ptw = int((int(COLUMNS) - 50) * 0.1)
            log_format = "{0: <" + str(fw) + "} :: {1: <" + str(sw) + "} :: {2: <" + str(ptw) + "} :: {3}"
        except Exception:
            fw, sw, ptw = None, None, None
            log_format = "{0} :: {1} :: {2} :: {3}"
    else:
        log_format = ""

    @sink
    def _log_output(message):
        if MessageProxy.debug:
            logger.info(log_format.format(
                truncate(message.mail['From'], fw),
                truncate(message.mail['Subject'], sw),
                str(message._msg.get_tags()),
                ", ".join(message._add_tags | message._remove_tags)))
    return _log_output


def truncate(s, w):
    if w is None:
        return s

    if s is None or w <= 4:
        return ''

    s = ' '.join(str(s).split())
    return s if len(s) < w else s[:w - 3] + "..."


def process_pipeline(query, pipeline):
    try:
        for msg in get_messages(query):
            msg.freeze()
            pipeline.send(msg)
            msg.thaw()

    except notmuch.errors.NullPointerError:
        logger.error("Query returned no results")
        pipeline.close()

@click.group()
@click.option('--debug/--no-debug', default=False)
@click.option('--dryrun/--no-dryrun', default=False)
def main(dryrun, debug):
    MessageProxy.debug = debug
    MessageProxy.dryrun = dryrun

    if debug:
        logger.setLevel(logging.DEBUG)

@main.command()
@click.argument('query', default='tag:new and path:"**"')
def sync(query):
    logger.debug("Query: {}".format(query))
    process_pipeline(query, Pipeline([sync_gmail_tags,
                                      add_mention,
                                      remove_new,
                                      log_output()]))

@main.command()
@click.argument('query', default='tag:new and path:"**"')
def sync_keywords(query):
    logger.debug("Query: {}".format(query))
    process_pipeline(query, Pipeline([sync_gmail_keywords,
                                      drop]))


if __name__ == "__main__":
    main()
