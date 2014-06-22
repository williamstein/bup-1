#!/usr/bin/env python
import sys, stat, subprocess
from bup import options, git, vfs
from bup.helpers import *
import datetime

def _mangle_name(name, bupmode):
    """Mangle a file name depending on its bupmode"""
    assert(bupmode in [git.BUP_NORMAL, git.BUP_CHUNKED])
    if bupmode == git.BUP_CHUNKED:
        return name + '.bup'
    elif name.endswith('.bup') or name[:-1].endswith('.bup'):
        return name + '.bupl'
    else:
        return name

def get_candidates(part):
    """Return possibilities in which bup could have mangled a path part"""
    return [_mangle_name(part, git.BUP_NORMAL),
            _mangle_name(part, git.BUP_CHUNKED)]

def demangle_path_part(part):
    """Demangle a path part"""
    if part == '.bupm':
        return '.bupm', git.BUP_CHUNKED
    else:
        return git.demangle_name(part)

def demangle_path(path):
    """demangle an entire path; this requires demangling of all parts
    as well as removing bup-internal subdirectories of BUP_CHUNKED
    files"""
    bupmode = git.BUP_NORMAL
    head = path
    demangled_parts = []
    while head != '':
        head, tail = os.path.split(head)
        demangled_tail, tail_bupmode = demangle_path_part(tail)
        if tail_bupmode == git.BUP_CHUNKED:
            # only one path part can be CHUNKED or else there is a problem:
            assert(bupmode == git.BUP_NORMAL)
            # since this part is CHUNKED, the entire path is CHUNKED:
            bupmode = git.BUP_CHUNKED
            # all subdirectories are bup internals and we don't want
            # to see them here:
            demangled_parts = []

        demangled_parts = [demangled_tail] + demangled_parts

    demangled_path = ''
    for part in demangled_parts:
        demangled_path = os.path.join(demangled_path, part)

    return demangled_path, bupmode

def _git_line_reader(argv, separator = '\n'):
    """Call a process and generate its output line by line; if
    separator is given, lines are separated by it"""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, preexec_fn = git._gitenv)
    carryover_line = None
    buffer = ''
    while 1:
        read = p.stdout.read(65636)
        if read == '':
            break
        else:
            buffer += read

        while 1:
            nextindex = buffer.find(separator)
            if nextindex < 0:
                break
            line = buffer[:nextindex]
            yield line
            buffer = buffer[nextindex+1:]
    if buffer != '':
        # if buffer is not empty, the string did not end with the
        # separator. For safety we assume this cannot happen:
        assert(buffer == '')
        # (could also yield buffer)
    git._git_wait(repr(argv), p)

def get_loglines(
        ref = None,
        format = '',
        path = '',
        after = None,
        before = None,
        ):
    """Generate a list of all commits that touch a path."""

    # Currently we can't handle newlines
    assert(format.find('\n') < 0 and format.find('%n') < 0)

    if not ref:
        ref = '--branches'

    command = ['git', 'log',
            ref,
            '--format=%H%at ' + format,
            '--date-order']

    if after:
        command += ['--after', after]
    if before:
        command += ['--before', before]

    if path != '':
        command += ['--'] + get_candidates(path)

    lines = _git_line_reader(command)
    for line in lines:
        if line != '':
            commit = line[:40]
            timestampend = line.find(' ')
            unixtime = line[40:timestampend]
            output = line[timestampend+1:]
            yield commit, unixtime, output


def filter_sparse(loglines):
    """Only output references to some saves; fewer the further past."""
    last_date = None
    possibly_oldest = None
    for commit, date, other in loglines:

        now = datetime.datetime.now()
        commitdate = datetime.datetime.fromtimestamp(float(date))
        delta1 = (now - commitdate).total_seconds()

        # always keep the most recent save
        if last_date == None:
            last_date = commitdate
            yield commit, date, other
            continue

        delta2 = (last_date - commitdate).total_seconds()

        keepthis = False

        # keep one save per 15 minutes for the last day
        if delta1 <= 24 * 3600:
            if delta2 >= 3600 / 4:
                keepthis = True

        # keep one save per hour for the last three days
        elif delta1 <= 3 * 24 * 3600:
            if delta2 >= 3600:
                keepthis = True

        # keep one save per day for the last week
        elif delta1 <= 7 * 24 * 3600:
            if delta2 >= 24 * 3600:
                keepthis = True

        # keep one save per month for the last 12 months
        elif delta1 <= 12 * 30 * 24 * 3600 :
            if delta2 >= 30 * 3600:
                keepthis = True

        # keep at least one save per year
        else:
            if delta2 >= 12 * 30 * 24 * 3600:
                keepthis = True

        if keepthis:
            possibly_oldest = None
            last_date = commitdate
            yield commit, date, other
        else:
            possibly_oldest = commit, date, other

    # in the end, also always yield the oldest commit
    if possibly_oldest:
        yield possibly_oldest

def filter_max_count(loglines, max_count):
    """Yields the first max_count lines of loglines"""
    num = 0
    for line in loglines:
        if num < max_count:
            yield line
            num += 1
        else:
            break

def get_changed_files(commit):
    """Generate all paths changed in a commit."""
    pathlines = _git_line_reader(['git', 'diff-tree',
        '--root',
        '-z',
        '--no-commit-id',
        '--name-only',
        '-r', commit], '\0')
    previous_path = ''
    for line in pathlines:
        name, bupmode = demangle_path(line)
        # remove duplicates; this is useful for BUP_CHUNKED files
        if name != previous_path and not name.endswith('.bupm'):
            previous_path = name
            yield name

optspec = """
bup log [options] [path]
--
max-count,n= limit the number of saves to output
after,since= show saves more recent than a specific date
before,until= show saves older than a specific date
sparse list a subset of saves; fewer the further past
ref=    only display saves reachable from ref (branch, commit, or tag)
shortstat show the number of files changed
changes list all files changed in a given commit
format= see 'git help log' for formatting options
reverse list saves in chronological order, starting from the oldest
"""

handle_ctrl_c()

o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

git.check_repo_or_die()

git.repo()

if opt.ref:
    try:
        git._git_capture(['git', 'rev-parse', str(opt.ref)])
    except git.GitError, a:
        branches = _git_line_reader(['git', 'for-each-ref',
            '--format=%(refname)',
            'refs/heads/'])
#        branches = (branch.strip() for branch in branches)
        branches = (branch[11:] for branch in branches)
        o.fatal('Ref is not valid. Valid branches are:\n  %s' % '\n  '.join(branches))
else:
    opt.ref = None

if opt.max_count:
    opt.max_count = int(opt.max_count)

if not opt.format:
    opt.format = '%H %Cgreen%ar%Creset'

if not extra:
    extra = ['/']

path = os.path.abspath(extra[0])
path = path[1:]

# Retrieve the loglines from git log
loglines = get_loglines(
        ref = opt.ref,
        format = opt.format,
        path = path,
        after = opt.after,
        before = opt.before,
        )

# Apply the filters to loglines:

if opt.sparse:
    loglines = filter_sparse(loglines)

if opt.reverse:
    # This may cause problems if there are really many commits:
    loglines = list(loglines)
    loglines.reverse()

if opt.max_count != None:
    loglines = filter_max_count(loglines, opt.max_count)

for commit, unixtime, output in loglines:

    if opt.changes or not opt.shortstat:
        savename = time.strftime('%Y-%m-%d-%H%M%S',
                                 time.localtime(float(unixtime)))
        print savename, output

    if opt.shortstat or opt.changes:
        changed_files = get_changed_files(commit)
        changes = 0

        for file in changed_files:
            changes += 1
            if opt.changes:
                print '  ' + file

        if changes == 1:
            shortstat_msg = ' (1 file changed)'
        else:
            shortstat_msg = ' (' + str(changes) + ' files changed)'

    if opt.shortstat and not opt.changes:
        print output + shortstat_msg
    if opt.changes and opt.shortstat:
        print shortstat_msg

    sys.stdout.flush()


if saved_errors:
    log('warning: %d errors encountered\n' % len(saved_errors))
    sys.exit(1)
