#!/usr/bin/env python
import os, re, stat, sys, time
from bup import git, options, client, vfs
from bup.helpers import handle_ctrl_c, hostname, log, saved_errors
from bup.helpers import hostname, userfullname, username


def find_vfs_item(item, repo_dir, vfs_top):
    # Return None or (type, node) or where type is 'root', 'branch',
    # 'save', or 'other'.
    # FIXME: rework the VFS to make this easier/cleaner?
    try:
        n = vfs_top.lresolve(item)
        if isinstance(n, vfs.FakeSymlink) \
           and n.parent and isinstance(n.parent, vfs.BranchList):
            return ('save', n.dereference())
        if isinstance(n, vfs.RefList):
            return ('root', n)
        if isinstance(n, vfs.BranchList):
            return ('branch', n)
        else:
            return ('other', n)
    except vfs.NodeError, ex:
        return None


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
def parse_commit(content):
    commit_match = re.match(_commit_rx, content)
    if not commit_match:
        raise Exception('cannot parse commit %r' % content)
    matches = commit_match.groupdict()
    return {'tree' : matches['tree'],
            'parents' : re.findall(_parent_hash_rx, matches['parents']),
            'author-name' : matches['author_name'],
            'author-mail' : matches['author_mail'],
            'author-sec' : int(matches['asec']),
            'author-offset' : parse_tz_offset(matches['atz']),
            'committer-name' : matches['committer_name'],
            'committer-mail' : matches['committer_mail'],
            'committer-sec' : int(matches['csec']),
            'committer-offset' : parse_tz_offset(matches['ctz']),
            'message' : matches['message']}


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
        tree_id = commit_items['tree']
        for x in walk_object(cat_pipe, tree_id, verbose, parent_path, writer):
            yield x
        parents = commit_items['parents']
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
                    elif verbose > 2: # (and BUP_CHUNKED)
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


def append_tree(name, hash, parent, cp, writer, opt):
    # Assumes that hash has already been verified to be a tree.
    get_random_item(name, hash, cp, writer, opt)
    msg = 'bup save\n\nGenerated by command:\n%r\n' % sys.argv
    userline = '%s <%s@%s>' % (userfullname(), username(), hostname())
    now = time.time()
    return (writer.new_commit(hash.decode('hex'), parent,
                              userline, now, None, userline, now, None, msg),
            hash.decode('hex'))


def append_commit(name, hash, parent, cp, writer, opt):
    now = time.time()
    items = get_commit_items(hash, cp)
    tree = items['tree'].decode('hex')
    author = '%s <%s>' % (items['author-name'], items['author-mail'])
    author_time = (items['author-sec'], items['author-offset'])
    committer =  '%s <%s@%s>' % (userfullname(), username(), hostname())
    get_random_item(name, hash, cp, writer, opt)
    c = writer.new_commit(tree, parent,
                          author, items['author-sec'], items['author-offset'],
                          committer, now, None,
                          items['message'])
    return (c, tree)


def append_branch(item, hash, dest_tip, parent, repo, cp, writer, opt, fatal):
    # Hash must refer to the branch tip commit.
    commits = [c for d, c in git.rev_list(hash, repo_dir=repo)]
    if not dest_tip or dest_tip in commits:
        # Can fast forward.
        get_random_item(item, hash, cp, writer, opt)
        return (hash.decode('hex'),
                get_commit_items(hash, cp)['tree'].decode('hex'))
    else:
        if not opt.force:
            fatal('%r is not an extension of %r' % (item, opt.name))
        commits.reverse()
        last_c, tree = parent, None
        for commit in commits:
            last_c, tree = append_commit(item, commit.encode('hex'), last_c,
                                         cp, writer, opt)
        assert(tree != None)
        return (last_c, tree)


def append_item(item, dest_tip, parent, vfs_top, repo, cp, writer, opt, fatal):
    vfs_item = find_vfs_item(item, repo, vfs_top)
    if vfs_item:
        type, node = vfs_item
        if type == 'save':
            return append_commit(item, node.hash.encode('hex'), parent,
                                 cp, writer, opt)
        elif type == 'branch':
            return append_branch(item, node.hash.encode('hex'), dest_tip,
                                 parent, repo, cp, writer, opt, fatal)
        else:
            fatal('cannot append %r to a branch' % item)

    # Not a VFS path; see if it's a tag or hash.
    # Should this handle abbreviated hashes (c.f. git rev-parse or similar)?
    hash = git.rev_parse(item, repo_dir = repo)
    if not hash:
        fatal('cannot find %r in repository' % item)
    hex_hash = hash.encode('hex')
    item_it = cp.get(hex_hash)
    type = item_it.next()
    del item_it # So we're not "in progress" as far as git.py's concerned.
    if type == 'commit':
        return append_commit(item, hex_hash, parent, cp, writer, opt)
    elif type == 'tree':
        return append_tree(item, hex_hash, parent, cp, writer, opt)
    else:
        fatal('cannot append %r to a branch' % item)


class LocalRepo:
    def close(self):
        pass
    def read_ref(self, name):
        return git.read_ref(name)
    def update_ref(self, name, newval, oldval):
        return git.update_ref(name, newval, oldval)
    def packwriter(self, compression_level):
        # Returns a new one every time -- caller closes.
        return git.PackWriter(compression_level = compression_level)


class RemoteRepo:
    def __init__(self, remote_name):
        self._client = client.Client(remote_name)
    def close(self):
        self._client.close()
    def read_ref(self, name):
        return self._client.read_ref(name)
    def update_ref(self, name, newval, oldval):
        return self._client.update_ref(name, newval, oldval)
    def packwriter(self, compression_level):
        # Returns a new one every time -- caller closes.
        return self._client.new_packwriter(compression_level = compression_level)


optspec = """
bup get [-s SRC_REPO_PATH] <(SET | OBJECT) ...>
bup get -n NAME [-s SRC_REPO_PATH] <(SET | COMMIT | TREE) ...>
--
s,source=  path to the source repository (defaults to BUP_DIR)
r,remote=  hostname:/path/to/repo of remote destination repository
t,tree     output a tree id (for each SET)
c,commit   output a commit id (for each SET)
n,name=    name of backup set to update (if any)
force      update branches even if they're not ancestors of their sources
v,verbose  increase log output (can be used more than once)
q,quiet    don't show progress meter
bwlimit=   maximum bytes/sec to transmit to server
#,compress=  set compression level to # (0-9, 9 is highest) [1]
"""

handle_ctrl_c()

o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

git.check_repo_or_die()

if not len(extra):
    o.fatal('no items to get')

get_items = extra
src_repo = opt.source or git.repo()

if opt.bwlimit:
    client.bwlimit = parse_num(opt.bwlimit)

if opt.name:
    if opt.name.startswith('.'):
        o.fatal("branch names cannot start with a '.'")
    elif '/' in opt.name:
        o.fatal("branch names cannot contain '/'")

is_reverse = os.environ.get('BUP_SERVER_REVERSE')
if is_reverse and opt.remote:
    o.fatal("don't use -r in reverse mode; it's automatic")

if opt.remote or is_reverse:
    dest_repo = RemoteRepo(opt.remote)
else:
    dest_repo = LocalRepo()
writer = dest_repo.packwriter(compression_level=opt.compress)

src_top = vfs.RefList(None, repo_dir = src_repo)
src_cp = vfs.cp(src_repo)

updated_refs = {} # ref_name -> (original_ref, tip_commit(bin))
no_ref_info = (None, None)
if opt.name:
    trees = {} # ref_name -> tip_tree(bin)
    # Everything must be treeish and will be appended to opt.name.
    ref_name = 'refs/heads/%s' % opt.name
    for item in get_items:
        orig_ref, cur_ref = updated_refs.get(ref_name, no_ref_info)
        if not orig_ref:
            orig_ref = dest_repo.read_ref(ref_name)
        commit, tree = append_item(item, orig_ref, cur_ref, src_top, src_repo,
                                   src_cp, writer, opt, o.fatal)
        updated_refs[ref_name] = (orig_ref, commit)
        trees[ref_name] = tree
        # We know it's treeish, so print it with a trailing slash.
        if opt.verbose:
            if not item.endswith('/'):
                log('%s/\n' % item)
            else:
                log('%s\n' % item)
    if get_items:
        if opt.tree:
            print trees[ref_name].encode('hex')
        if opt.commit:
            orig_ref, cur_ref = updated_refs[ref_name]
            print cur_ref.encode('hex')
else:
    # Update existing branch, or pull random item.
    for item in get_items:
        vfs_item = find_vfs_item(item, src_repo, src_top)
        if vfs_item:
            type, node = vfs_item
            if type == 'branch':
                ref_name = 'refs/heads/%s' % node.name
                orig_ref, cur_ref = updated_refs.get(ref_name, no_ref_info)
                if orig_ref:
                    log('already fetched branch %r (skipping)' % node.name)
                else:
                    orig_ref = dest_repo.read_ref(ref_name)
                    commit, tree = append_branch(item, node.hash.encode('hex'),
                                                 orig_ref, cur_ref,
                                                 src_repo, src_cp, writer,
                                                 opt, o.fatal)
                    if opt.tree:
                        print tree.encode('hex')
                    if opt.commit:
                        print commit.encode('hex')
                    updated_refs[ref_name] = (orig_ref, commit)
                    if opt.verbose:
                        # We know it's treeish, so print it with a trailing slash.
                        if not item.endswith('/'):
                            log('%s/\n' % item)
                        else:
                            log('%s\n' % item)
            elif node.hash == vfs.EMPTY_SHA:
                o.fatal('cannot fetch %r (no hash)' % item)
            else:
                get_random_item(item, node.hash.encode('hex'),
                                src_cp, writer, opt)
                if opt.verbose:
                    if type in ('root', 'save'):
                        if not item.endswith('/'):
                            log('%s/\n' % item)
                        else:
                            log('%s\n' % item)
                    else:
                        log('%s\n' % item)
        else:
            # Not a VFS path, see if it's a tag or hash.
            # Should this handle abbreviated hashes (c.f. git rev-parse or similar)?
            hash = git.rev_parse(item, repo_dir=src_repo)
            if hash:
                hex_hash = hash.encode('hex')
                get_random_item(item, hex_hash, src_cp, writer, opt)
                if opt.verbose:
                    item_it = src_cp.get(hex_hash)
                    type = item_it.next()
                    del item_it
                    if type in ('commit', 'tree'):
                        if not item.endswith('/'):
                            log('%s/\n' % item)
                        else:
                            log('%s\n' % item)
                    else:
                        log('%s\n' % item)
            else:
                o.fatal('cannot find %r in repository' % item)

writer.close()  # Must close before we can update the ref(s).

for ref_name, info in updated_refs.iteritems():
    orig_ref, new_ref = info
    dest_repo.update_ref(ref_name, new_ref, orig_ref)
dest_repo.close()

if saved_errors:
    log('WARNING: %d errors encountered while saving.\n' % len(saved_errors))
    sys.exit(1)
