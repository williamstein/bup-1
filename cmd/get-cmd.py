#!/usr/bin/env python
import os, re, sys, time
from bup import git, options, client, vfs
from bup.helpers import handle_ctrl_c, hostname, log, saved_errors
from bup.helpers import hostname, userfullname, username

# FIXME: do we need to exclude src == dest (more specifically, could
# it cause any serious trouble)?

# FIXME: derived from http://git.rsbx.net/Documents/Git_Data_Formats.txt
# Make sure that's authoritative.
_start_end_char = r'[^ .,:;<>"\'\0\n]'
_content_char = r'[^\0\n<>]'
_safe_str_rx = '(?:%s{1,2}|(?:%s%s*%s))' \
    % (_start_end_char,
       _start_end_char, _content_char, _start_end_char)
_date_rx = r'\d+ [-+]\d\d[0-5]\d'
_parent_rx = r'(?:parent [abcdefABCDEF0123456789]{40}\n)'
_commit_rx = re.compile(r'''tree (?P<tree>[abcdefABCDEF0123456789]{40})
(?P<parents>%s*)author (?P<author_name>%s) <(?P<author_mail>%s)> (?P<author_date>%s)
committer (?P<committer_name>%s) <(?P<committer_mail>%s)> (?P<committer_date>%s)

(?P<message>(?:.|\n)*)''' % (_parent_rx,
                         _safe_str_rx, _safe_str_rx, _date_rx,
                         _safe_str_rx, _safe_str_rx, _date_rx))

def parse_commit(content):
    # Ignore parents for now.
    commit_match = re.match(_commit_rx, content)
    if not commit_match:
        raise Exception('cannot parse commit %r' % content)
    matches = commit_match.groupdict()
    return {'tree' : matches['tree'],
            'author-name' : matches['author_name'],
            'author-mail' : matches['author_mail'],
            'author-date' : matches['author_date'],
            'committer-name' : matches['committer_name'],
            'committer-mail' : matches['committer_mail'],
            'committer-date' : matches['committer_date'],
            'message' : matches['message']}


def tree_walker(cat_pipe, id):
    item_it = cat_pipe.get(id)
    type = item_it.next()
    # item_it is now an iterator over the object content.
    if type == 'blob':
        yield (type, item_it)
    elif type == 'commit':
        commit_file = ''.join(item_it)
        yield (type, [commit_file])
        tree_sha = parse_commit(commit_file)['tree']
        for tree_item in tree_walker(cat_pipe, tree_sha):
            yield tree_item
    elif type == 'tree':
        tree_file = ''.join(item_it)
        yield (type, [tree_file])
        for (mode, name, sha) in git.tree_decode(tree_file):
            for tree_item in tree_walker(cat_pipe, sha.encode('hex')):
                yield tree_item
    else:
        raise Exception('unexpected repository object type %r' % type)


optspec = """
bup get [-n NAME] [-s SRC_REPO_PATH] <(sha:SHA | save:PATH) ...>
--
s,source=  path to the source repository (defaults to BUP_DIR)
r,remote=  hostname:/path/to/repo of remote destination repository
t,tree     output a tree id (for each save:)
c,commit   output a commit id (for each save:)
n,name=    name of backup set to update (if any)
v,verbose  increase log output (can be used more than once)
q,quiet    don't show progress meter
bwlimit=   maximum bytes/sec to transmit to server
#,compress=  set compression level to # (0-9, 9 is highest) [1]
"""

handle_ctrl_c()

o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

git.check_repo_or_die()

if not (opt.tree or opt.commit or opt.name):
    o.fatal('use one or more of -t, -c, -n')
if not len(extra):
    o.fatal('no items to get')

get_items = extra
src_repo = opt.source or git.repo()

for item in get_items:
    if re.match(r'[abcdef0123456789]{40}$', item, re.IGNORECASE):
        continue
    elif re.match(r'/*[^/]+/[^/]+$', item):
        continue
    else:
        o.fatal('item %r must a /branch/revision or SHA' % item)

if opt.bwlimit:
    client.bwlimit = parse_num(opt.bwlimit)

if opt.name and opt.name.startswith('.'):
    o.fatal("'%s' is not a valid branch name" % opt.name)
refname = opt.name and 'refs/heads/%s' % opt.name or None

is_reverse = os.environ.get('BUP_SERVER_REVERSE')
if is_reverse and opt.remote:
    o.fatal("don't use -r in reverse mode; it's automatic")

if opt.remote or is_reverse:
    cli = client.Client(opt.remote)
    oldref = refname and cli.read_ref(refname) or None
    w = cli.new_packwriter()
else:
    cli = None
    oldref = refname and git.read_ref(refname) or None
    w = git.PackWriter(compression_level=opt.compress)

src_top = vfs.RefList(None, repo_dir = src_repo)
src_cp = vfs.cp(src_repo)

prevref = oldref
for item in get_items:
    if not '/' in item:
        # For now, that means it's a hash.
        for obj in repo_tree_walker(src_cp, item):
            type, data_it = obj
            w.maybe_write(type, ''.join(data_it))
    else:
        try:
            src_n = src_top.lresolve(item)
        except vfs.NodeError, ex:
            o.fatal('cannot find %r (%s)' % (item, ex))
        commit = src_n.dereference()
        commit_it = src_cp.get(commit.hash.encode('hex'))
        assert(commit_it.next() == 'commit')
        commit_content = ''.join(commit_it)
        commit_items = parse_commit(commit_content)
        for obj in tree_walker(src_cp, commit_items['tree']):
            type, data_it = obj
            w.maybe_write(type, ''.join(data_it))
        if opt.tree:
            print tree
        if opt.commit or opt.name:
            # FIXME: is this what we want?
            committer =  '%s <%s@%s>' % (userfullname(), username(), hostname())
            now = time.time()
            commit_date = '%d %s' % (now, time.strftime('%z', time.localtime(now)))
            new_commit = ['tree ' + commit_items['tree']]
            if prevref:
                new_commit.append('parent %s' % prevref.encode('hex'))
            authorline = '%s <%s> %s' % (commit_items['author-name'],
                                         commit_items['author-mail'],
                                         commit_items['author-date'])
            new_commit.extend([authorline,
                               'committer %s %s' % (committer, commit_date),
                               ''])
            new_commit.extend(commit_items['message'])
            commit = w.maybe_write('commit', '\n'.join(new_commit))
            prevref = commit
            if opt.commit:
                print commit.encode('hex')

w.close()  # must close before we can update the ref
        
if opt.name:
    if cli:
        cli.update_ref(refname, commit, oldref)
    else:
        git.update_ref(refname, commit, oldref)

if cli:
    cli.close()

if saved_errors:
    log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
    sys.exit(1)
