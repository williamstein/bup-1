#!/usr/bin/env python
import os, re, stat, sys, time
from collections import namedtuple
from functools import partial
from bup import git, options, client, helpers, vfs
from bup.helpers import add_error, debug1, handle_ctrl_c, log, saved_errors
from bup.helpers import hostname, userfullname, username


optspec = """
bup get [-s SRC_REPO_PATH] <(SRC:DEST | SRC[:SRC_OPTS]::[DEST][:DEST_OPTS]) ...>
--
s,source=  path to the source repository (defaults to BUP_DIR)
r,remote=  hostname:/path/to/repo of remote destination repository
t,print-trees     output a tree id (for each SET)
c,print-commits   output a commit id (for each SET)
print-tags  output an id for each tag
force      update branches even if they're not ancestors of their sources
v,verbose  increase log output (can be used more than once)
q,quiet    don't show progress meter
bwlimit=   maximum bytes/sec to transmit to server
#,compress=  set compression level to # (0-9, 9 is highest) [1]
"""

is_reverse = os.environ.get('BUP_SERVER_REVERSE')
if is_reverse:
    orig_stderr = sys.stderr
    sys.stderr = helpers.TaggedOutput(orig_stderr, 'e')
    sys.stdout = helpers.TaggedOutput(orig_stderr, 'o')


class LocalRepo:
    def __init__(self, dir=None):
        self.update_ref = partial(git.update_ref, repo_dir=dir)
        self._vfs_top = vfs.RefList(None, repo_dir=dir)
        self.path_info = lambda x: vfs.path_info(x, self._vfs_top)
    def close(self):
        pass
    def is_local():
        return True
    def packwriter(self, compression_level):
        # FIXME: this doesn't support repo_dir!
        # Returns a new one every time -- caller closes.
        return git.PackWriter(compression_level=compression_level)


class RemoteRepo:
    def __init__(self, remote_name):
        self._client = client.Client(remote_name)
        self.path_info = self._client.path_info
        self.update_ref = self._client.update_ref
    def client(self):
        return self._client
    def close(self):
        self._client.close()
    def is_local():
        return True
    def packwriter(self, compression_level):
        # Returns a new one every time -- caller closes.
        return self._client.new_packwriter(compression_level=compression_level)


def parse_tz_offset(s):
    tz_off = int(s[1:3] * 60) + int(s[3:5])
    if s[0] == '-':
        return - tz_off
    return tz_off


# FIXME: derived from http://git.rsbx.net/Documents/Git_Data_Formats.txt
# Make sure that's authoritative.
_start_end_char = r'[^ .,:;<>"\'\0\n]'
_content_char = r'[^\0\n<>]'
_safe_str_rx = '(?:%s{1,2}|(?:%s%s*%s))' \
    % (_start_end_char,
       _start_end_char, _content_char, _start_end_char)
_tz_rx = r'[-+]\d\d[0-5]\d'
_parent_rx = r'(?:parent [abcdefABCDEF0123456789]{40}\n)'
_commit_rx = re.compile(r'''tree (?P<tree>[abcdefABCDEF0123456789]{40})
(?P<parents>%s*)author (?P<author_name>%s) <(?P<author_mail>%s)> (?P<asec>\d+) (?P<atz>%s)
committer (?P<committer_name>%s) <(?P<committer_mail>%s)> (?P<csec>\d+) (?P<ctz>%s)

(?P<message>(?:.|\n)*)''' % (_parent_rx,
                             _safe_str_rx, _safe_str_rx, _tz_rx,
                             _safe_str_rx, _safe_str_rx, _tz_rx))
_parent_hash_rx = re.compile(r'\s*parent ([abcdefABCDEF0123456789]{40})\s*')


CommitInfo = namedtuple('CommitInfo', ['tree', 'parents',
                                       'author_name', 'author_mail',
                                       'author_sec', 'author_offset',
                                       'committer_name', 'committer_mail',
                                       'committer_sec', 'committer_offset',
                                       'message'])

def parse_commit(content):
    commit_match = re.match(_commit_rx, content)
    if not commit_match:
        raise Exception('cannot parse commit %r' % content)
    matches = commit_match.groupdict()
    return CommitInfo(tree=matches['tree'],
                      parents=re.findall(_parent_hash_rx, matches['parents']),
                      author_name=matches['author_name'],
                      author_mail=matches['author_mail'],
                      author_sec=int(matches['asec']),
                      author_offset=parse_tz_offset(matches['atz']),
                      committer_name=matches['committer_name'],
                      committer_mail=matches['committer_mail'],
                      committer_sec=int(matches['csec']),
                      committer_offset=parse_tz_offset(matches['ctz']),
                      message=matches['message'])


def get_commit_items(id, cp):
    commit_it = cp.get(id)
    assert(commit_it.next() == 'commit')
    commit_content = ''.join(commit_it)
    return parse_commit(commit_content)


def walk_object(cat_pipe, id, verbose=None, parent_path=[], writer=None):
    # Yield everything reachable from id via cat_pipe, stopping
    # whenever we hit something writer already has.  Produce (id, type
    # data) for each item.  Since maybe_write() can't accept an
    # iterator, join()ing the data here doesn't hurt anything.
    item_it = cat_pipe.get(id)
    type = item_it.next()
    data = ''.join(item_it)
    id = git.calc_hash(type, data)
    if writer and writer.exists(id):
        return
    if type == 'blob':
        yield (id, type, data)
    elif type == 'commit':
        yield (id, type, data)
        commit_items = parse_commit(data)
        tree_id = commit_items.tree
        for x in walk_object(cat_pipe, tree_id, verbose, parent_path, writer):
            yield x
        parents = commit_items.parents
        for pid in parents:
            for x in walk_object(cat_pipe, pid, verbose, parent_path, writer):
                yield x
    elif type == 'tree':
        yield (id, type, data)
        for (mode, name, ent_id) in git.tree_decode(data):
            if not verbose > 1:
                for x in walk_object(cat_pipe, ent_id.encode('hex'),
                                     writer=writer):
                    yield x
            else:
                demangled, bup_type = git.demangle_name(name)
                sub_path = parent_path + [demangled]
                # Don't print the sub-parts of chunked files.
                sub_v = verbose if bup_type == git.BUP_NORMAL else None
                for x in walk_object(cat_pipe, ent_id.encode('hex'),
                                     sub_v, sub_path, writer):
                    yield x
                if stat.S_ISDIR(mode):
                    if verbose > 1 and bup_type == git.BUP_NORMAL:
                        log('%s/\n' % '/'.join(sub_path))
                    elif verbose > 2:  # (and BUP_CHUNKED)
                        log('%s\n' % '/'.join(sub_path))
                elif verbose > 2:
                    log('%s\n' % '/'.join(sub_path))
    else:
        raise Exception('unexpected repository object type %r' % type)


def get_random_item(name, hash, cp, writer, opt):
    for id, type, data in walk_object(cp, hash, opt.verbose, [name],
                                      writer=writer):
        # Passing writer to walk_object ensures that writer.exists(id)
        # is false.  Otherwise, write() would fail.
        writer.write(id, type, data)


def add_tree(src_name, src_hash, dest_name, dest_hash, dest_method,
             cp, writer, opt, fatal):
    # Assumes that hash has already been verified to be a tree.
    if dest_method == 'ff':
        fatal('%r is a tree; cannot fast forward destination' % src_name)
    get_random_item(src_name, src_hash, cp, writer, opt)
    parent = dest_hash
    if dest_method == 'force':
        parent = None
    msg = 'bup save\n\nGenerated by command:\n%r\n' % sys.argv
    userline = '%s <%s@%s>' % (userfullname(), username(), hostname())
    now = time.time()
    commit = writer.new_commit(src_hash.decode('hex'), parent,
                               userline, now, None,
                               userline, now, None, msg)
    return commit, src_hash.decode('hex')


def append_commit(name, hash, parent, cp, writer, opt):
    now = time.time()
    items = get_commit_items(hash, cp)
    tree = items.tree.decode('hex')
    author = '%s <%s>' % (items.author_name, items.author_mail)
    author_time = (items.author_sec, items.author_offset)
    committer = '%s <%s@%s>' % (userfullname(), username(), hostname())
    get_random_item(name, hash, cp, writer, opt)
    c = writer.new_commit(tree, parent,
                          author, items.author_sec, items.author_offset,
                          committer, now, None,
                          items.message)
    return (c, tree)


def add_commit(src_name, src_hash, dest_name, dest_hash, dest_method,
               repo, cp, writer, opt, fatal):
    commit_items = get_commit_items(src_hash, cp)
    if dest_method == 'force':
        get_random_item(src_name, src_hash, cp, writer, opt)
        return src_hash.decode('hex'), commit_items.tree.decode('hex')
    if dest_method == 'append':
        return append_commit(src_name, src_hash, dest_hash, cp, writer, opt)
    assert(dest_method == 'ff')
    commits = [c for d, c in git.rev_list(src_hash, repo_dir=repo)]
    if not dest_hash or dest_hash in commits:
        # Can fast forward.
        get_random_item(src_name, src_hash, cp, writer, opt)
        return src_hash.decode('hex'), commit_items.tree.decode('hex')
    if dest_method == 'ff':
        fatal('%r is not an extension of %r' % (src_name, dest_name))
    return append_commit(src_name, src_hash, dest_hash, cp, writer, opt)


def add_branch(src_name, src_hash, dest_name, dest_hash, dest_method,
               repo, cp, writer, opt, fatal):

    def append_commits(commits):
        last_c, tree = dest_hash, None
        for commit in commits:
            last_c, tree = append_commit(src_name, commit.encode('hex'), last_c,
                                         cp, writer, opt)
        assert(tree is not None)
        return last_c, tree

    if dest_method == 'force':
        commit_items = get_commit_items(src_hash, cp)
        get_random_item(src_name, src_hash, cp, writer, opt)
        return src_hash.decode('hex'), commit_items.tree.decode('hex')
    commits = [c for d, c in git.rev_list(src_hash, repo_dir=repo)]
    if dest_method == 'append':
        commits.reverse()
        return append_commits(commits)
    assert(dest_method == 'ff')
    if not dest_hash or dest_hash in commits:
        # Can fast forward.
        get_random_item(src_name, src_hash, cp, writer, opt)
        commit_items = get_commit_items(src_hash, cp)
        return src_hash.decode('hex'), commit_items.tree.decode('hex')
    if dest_method == 'ff':
        fatal('%r is not an extension of %r' % (src_name, dest_name))
    commits.reverse()
    return append_commits(commits)


def parse_target(s, item, kind):
    # Return (name, opts).
    parts = s.split(':')
    if len(parts) > 2:
        o.fatal('invalid %r in %r (more than one ":")' % (kind, item))
    dest_name = None
    if len(parts) == 1:
        return parts[0], None
    elif len(parts) == 2:
        return parts


Spec = namedtuple('Spec', ['arg', 'src', 'dest', 'anonymous', 'method'])


dest_opts_rx = re.compile(r'(([_]?[+=f]?)|([+=f]?[_]?))$')

def parse_target_arg(arg, fatal):
    parts = arg.split('::')
    if len(parts) > 2:
        fatal('invalid item %r (more than one "::")' % arg)
    src_opts, dest_opts = None, None
    if len(parts) == 1:  # No ::
        parts = arg.split(':')
        if len(parts) > 2:
            fatal('invalid item %r (more than one ":")' % arg)
        dest_name = None
        if len(parts) == 1:
            src_name = parts[0]
        elif len(parts) == 2:
            src_name, dest_name = parts
    else:  # "get foo:t::bar:t="
        src_name, src_opts = parse_target(parts[0], arg, 'source')
        dest_name, dest_opts = parse_target(parts[1], arg, 'destination')
    if not src_name:
        fatal('no source name in %r' % arg)
    if src_opts:
        fatal('source options specififed in %r' % arg)
    # Check source and destination option syntax.
    if dest_opts and not dest_opts_rx.match(dest_opts):
        fatal("invalid destination option string (%r)" % dest_opts)
    method = None
    if dest_opts:
        if 'f' in dest_opts:
            method = 'ff'
        elif '=' in dest_opts:
            method = 'force'
        elif '+' in dest_opts:
            method = 'append'
    anonymous = dest_opts and '_' in dest_opts
    if anonymous:
        if dest_name:
            fatal('specified destination name and "_" in %r' % arg)
        if method:
            fatal('anonymous destination with method in %r' % arg)
    return Spec(arg=arg,
                src=src_name, dest=dest_name,
                anonymous=dest_opts and '_' in dest_opts,
                method=method)


Loc = namedtuple('Loc', ['type', 'hash', 'path'])
default_loc = Loc(None, None, None)


# FIXME: change all the code to handle path_info() types directly
# (which would allow log_item() to handle chunked-files as files)?
def find_vfs_item(name, repo):
    info = repo.path_info([name])
    if not info[0]:
        return None
    path, id, type = info[0]
    if type in ('dir', 'chunked-file'):
        type = 'tree'
    elif type == 'file':
        type = 'blob'
    return Loc(type=type, hash=id, path=path)


Target = namedtuple('Target', ['spec', 'src', 'dest'])


def loc_desc(loc):
    if loc and loc.hash:
        loc = loc._replace(hash=loc.hash.encode('hex'))
    return str(loc)


def resolve_targets(specs, src_repo, src_vfs, src_dir, src_cp, dest_repo, fatal):
    resolved_items = []
    for spec in specs:
        debug1('initial-spec: %s\n' % str(spec))
        src = find_vfs_item(spec.src, src_repo)
        if not src:
            fatal('cannot find source for %r' % spec.arg)
        if src.hash == vfs.EMPTY_SHA.encode('hex'):
            fatal('cannot find source for %r (no hash)' % spec.arg)
        if src.type == 'root':
            fatal('cannot fetch entire repository for %r' % spec.arg)

        debug1('src: %s\n' % loc_desc(src))

        if not spec.dest and not spec.anonymous:
            # Pick a default dest.
            if src.type == 'branch':
                spec = spec._replace(dest=spec.src)
            elif src.type == 'save':
                try:
                    save_node = src_vfs.lresolve(spec.src)
                except vfs.NodeError, ex:
                    pass
                if not save_node:
                    fatal('%r has vanished from the source VFS' % spec.src)
                spec = spec._replace(dest=save_node.parent.fullname())
            elif src.path.startswith('/.tag/'):  # Dest defaults to the same.
                spec = spec._replace(dest=spec.src)

        if not spec.dest and spec.method:
            fatal('method specified for anonymous destination in %r' % spec.arg)

        # At this point, if there's no dest name, then we're not going
        # to be writing a ref.  If there is a dest name, it will be
        # interpreted as either a branch or tag, depending on whether
        # or not it starts with .tag/.

        if spec.dest:
            dest = find_vfs_item(spec.dest, dest_repo)
        else:
            dest = None

        if not dest and spec.dest:  # Clean up the name for the path.
            norm_dest = os.path.normpath(spec.dest)
            if norm_dest.startswith('/'):
                dest = default_loc._replace(path=norm_dest)
            else:
                dest = default_loc._replace(path='/' + norm_dest)

        if dest and dest.path \
           and dest.path.startswith('/.') \
           and not dest.path.startswith('/.tag/'):
            fatal('unsupported destination path %r in %r'
                  % (dest.path, spec.arg))

        debug1('dest: %s\n' % loc_desc(dest))

        # Now that we've found the source and the dest (if any), make
        # sure the request is reasonable.  Some guard test components
        # may be redundant (but hopefully make the indvidual cases
        # clearer).  If not set, dest_method will default to 'ff'
        # later.

        dest_type = dest and dest.type
        dest_path = dest and dest.path
        dest_method = spec.method
        if dest_type == 'root':
            fatal('cannot fetch directly to root for %r' % spec.arg)
        elif (src.type, dest_type) in (('branch', 'branch'),
                                       ('save', 'branch'),
                                       ('commit', 'branch'),
                                       ('tree', 'branch')):
            if src.type == 'tree' and dest_method != 'append':
                fatal('appending a tree to a branch requires "+" in %r' % spec.arg)
        elif dest and dest.hash and \
             dest_method != 'force' and dest_path.startswith('/.tag/'):
            fatal('cannot overwrite tag %r without force for %r' % (dest_path, spec.arg))
        elif src.type == 'blob' and dest_path:
            if not dest_path.startswith('/.tag/'):
                fatal('destination for blob is not a tag in %r' % spec.arg)
        elif src.type in ('branch', 'save', 'commit', 'tree') \
             and dest_path and (dest_method == 'force' or not dest.hash):
            # Everything can go to a branch or a tag, except blobs,
            # which must go to a tag.  But any non-tag will be assumed
            # to be a branch name.
            if src.type == 'tree' and not dest_path.startswith('/.tag/') \
               and dest_method != 'append':
                fatal('appending a tree to a branch requires "+" in %r' % spec.arg)
        elif not dest_path:
            # Just transfer a random item -- no implicit or explicit
            # dest i.e. "get ... HASH", "get ... /x/latest/some/file".
            pass
        else:
            # Catchall.
            dest_msg = dest_type and (' to a %s' % dest_type) or ''
            fatal("don't know how to fetch %s %r%s" % (src.type, spec.src, dest_msg))

        if dest_path:
            if dest_path.startswith('/.tag/'):
                if dest_method in ('ff', 'append'):
                    fatal('destination method in %r only applicable to a branch'
                          % spec.arg)
            else:
                if not dest_method:
                    spec = spec._replace(method='ff')

        resolved_items.append(Target(spec=spec, src=src, dest=dest))

    # FIXME: check for branch prefix overlap?
    # Now that we have all the items, check for duplicate tags.
    tags_targeted = set()
    for item in resolved_items:
        dest_path = item.dest and item.dest.path
        if dest_path:
            assert(dest_path.startswith('/'))
            if dest_path.startswith('/.tag/'):
                if dest_path in tags_targeted:
                    if item.spec.method != 'force':
                        fatal('cannot overwrite tag %r via %r' \
                              % (dest_path, item.spec.arg))
                else:
                    tags_targeted.add(dest_path)
    return resolved_items


def log_item(name, type, opt, tree=None, commit=None, tag=None):
    if tag and opt.print_tags:
        print tag.encode('hex')
    if tree and opt.print_trees:
        print tree.encode('hex')
    if commit and opt.print_commits:
        print commit.encode('hex')
    if opt.verbose:
        last = ''
        if type in ('root', 'branch', 'save', 'commit', 'tree'):
            if not name.endswith('/'):
                last = '/'
        log('%s%s\n' % (name, last))


handle_ctrl_c()

o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

if not len(extra):
    o.fatal('no items to get')

target_args = extra
target_specs = [parse_target_arg(x, o.fatal) for x in target_args]

git.check_repo_or_die()
src_dir = opt.source or git.repo()

if opt.bwlimit:
    client.bwlimit = parse_num(opt.bwlimit)

if is_reverse and opt.remote:
    o.fatal("don't use -r in reverse mode; it's automatic")

if opt.remote or is_reverse:
    dest_repo = RemoteRepo(opt.remote)
else:
    dest_repo = LocalRepo()

writer = dest_repo.packwriter(compression_level=opt.compress)
src_vfs = vfs.RefList(None, repo_dir=src_dir)
src_cp = vfs.cp(src_dir)
src_repo = LocalRepo(src_dir)

# Resolve and validate all sources and destinations, implicit or
# explicit, and do it up-front, so that we can fail before we start
# writing (for any obviously broken cases).
target_items = resolve_targets(target_specs,
                               src_repo, src_vfs, src_dir, src_cp, dest_repo, o.fatal)

updated_refs = {}  # ref_name -> (original_ref, tip_commit(bin))
no_ref_info = (None, None)

for item in target_items:

    debug1('get-spec: %s\n' % str(item.spec))
    debug1('get-src: %s\n' % loc_desc(item.src))
    debug1('get-dest: %s\n' % loc_desc(item.dest))

    dest_path = item.dest and item.dest.path
    if dest_path:
        if dest_path.startswith('/.tag/'):
            dest_ref = 'refs/tags/%s' % dest_path[6:]
        else:
            dest_ref = 'refs/heads/%s' % dest_path[1:]
    else:
        dest_ref = None

    src_name = item.spec.src
    src_hash = item.src.hash
    src_type = item.src.type
    dest_name = item.spec.dest
    dest_hash = item.dest and item.dest.hash
    method = item.spec.method

    orig_ref, cur_ref = updated_refs.get(dest_ref, no_ref_info)
    orig_ref = orig_ref or dest_hash
    cur_ref = cur_ref or dest_hash

    if not dest_ref or dest_ref.startswith('refs/tags/'):
        get_random_item(src_name, src_hash.encode('hex'), src_cp, writer, opt)
        if dest_ref:
            updated_refs[dest_ref] = (orig_ref, src_hash)
            log_item(src_name, src_type, opt, tag=src_hash)
        else:
            log_item(src_name, src_type, opt)
    elif src_type in ('branch', 'save', 'tree', 'commit'):
        if src_type == 'branch':
            commit, tree = add_branch(src_name, src_hash.encode('hex'),
                                      dest_name, cur_ref, method,
                                      src_dir, src_cp, writer, opt, o.fatal)
        elif src_type in ('commit', 'save'):
            commit, tree = add_commit(src_name, src_hash.encode('hex'),
                                      dest_name, cur_ref, method,
                                      src_dir, src_cp, writer, opt, o.fatal)
        elif src_type == 'tree':
            commit, tree = add_tree(src_name, src_hash.encode('hex'),
                                    dest_name, cur_ref, method,
                                    src_cp, writer, opt, o.fatal)
        if dest_ref:
            updated_refs[dest_ref] = (orig_ref, commit)
            if dest_ref.startswith('refs/tags/'):
                log_item(src_name, src_type, opt, tag=commit)
            else:
                log_item(src_name, src_type, opt, tree=tree, commit=commit)
        else:
            log_item(src_name, src_type, opt)
    else:
        # Should be impossible.
        assert(False)

writer.close()  # Must close before we can update the ref(s).

# Only update the refs at the very end, so that if something goes
# wrong above, the old refs will be undisturbed.
for ref_name, info in updated_refs.iteritems():
    orig_ref, new_ref = info
    try:
        dest_repo.update_ref(ref_name, new_ref, orig_ref)
        if opt.verbose:
            new_hex = new_ref.encode('hex')
            if orig_ref:
                orig_hex = orig_ref.encode('hex')
                log('updated %r (%s -> %s)\n' % (ref_name, orig_hex, new_hex))
            else:
                log('updated %r (%s)\n' % (ref_name, new_hex))
    except (git.GitError, client.ClientError), ex:
        add_error('unable to update ref %r: %s' % (ref_name, ex))

dest_repo.close()

if saved_errors:
    log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
    sys.exit(1)
