#!/usr/bin/env python

from bup import metadata, index, drecurse, options, git, hlinkdb
from bup.helpers import *
import atexit, os, signal, sys, time

# the following deamonize code is based on
# http://www.jejik.com/articles/2007/02/a_simple_unix_linux_daemon_in_python/
# with some slight modifications from some other implementations
def daemonize():
    """do the UNIX double-fork magic"""
    try:
        pid = os.fork()
        if pid > 0:
            # exit first parent
            sys.exit()
    except OSError as err:
        message = 'fork #1 failed: {0}'
        sys.exit(message.format(err))

    # decouple from parent environment
    os.chdir(os.sep)
    os.setsid()
    os.umask(0)

    # do second fork
    try:
        pid = os.fork()
        if pid > 0:
            # exit from second parent
            sys.exit()
    except OSError as err:
        message = 'fork #2 failed: {0}'
        sys.exit(message.format(err))

    # redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()
    si = open(os.devnull, 'r')
    so = open(opt.logfile, 'a+')
    se = open(opt.logfile, 'a+')

    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

    # make SIGTERM do a normal (proper) exit
    signal.signal(signal.SIGTERM, lambda signum, stack_frame: sys.exit())

    # setup for pidfile removal
    atexit.register(os.remove, opt.pidfile)
    # write pidfile
    with open(opt.pidfile, 'w+') as f:
        f.write(str(os.getpid()))

def check_pidfile():
    """check for the pidfile to see if the daemon is already running"""
    try:
        with open(opt.pidfile, 'r') as pf:
            pid = int(pf.read())
    except IOError:
        return # no pid file
    except ValueError:
        message = 'pidfile {0} contains non-numeric value'
        sys.exit(message.format(opt.pidfile))
    else:
        message = 'pidfile {0} already exist'
        sys.exit(message.format(opt.pidfile))

def kill_daemon(restart=False):
    """kill the daemon associated to the pidfile"""
    # get the pid from the pidfile
    try:
        with open(opt.pidfile, 'r') as pf:
            pid = int(pf.read())
    except IOError:
        if restart:
            return
        message = 'pidfile {0} does not exist'
        sys.exit(message.format(opt.pidfile))
    except ValueError:
        message = 'pidfile {0} contains non-numeric value'
        sys.exit(message.format(opt.pidfile))

    # try killing the daemon process
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as err:
        if err.errno == ernno.ESRCH:
            message = 'Removing stale pidfile {0}'
            print(message.format(opt.pidfile))
            os.remove(opt.pidfile)
        else:
            sys.exit(err)

_ri = None
_msw = None
_wi = None
_hlinks = None

def setup_globals(tmax):
    global _ri, _msw, _wi, _hlinks
    _ri = index.Reader(indexfile)
    _msw = index.MetaStoreWriter(indexfile+'.meta')
    _wi = index.Writer(indexfile, _msw, tmax)
    _hlinks = hlinkdb.HLinkDB(indexfile+'.hlink')

def save_index(tmax):
    _hlinks.prepare_save()

    if _ri.exists():
        _ri.save()
        _wi.flush()
        if _wi.count:
            wr = _wi.new_reader()
            mi = index.Writer(indexfile, _msw, tmax)

            for e in index.merge(_ri, wr):
                # FIXME: shouldn't we remove deleted entries eventually?  When?
                mi.add_ixentry(e)

            _ri.close()
            mi.close()
            wr.close()
        _wi.abort()
    else:
        _wi.close()

    _msw.close()
    _hlinks.commit_save()

def get_current(path):
    try:
        return next(iter(_ri.iter(name=path)))
    except StopIteration:
        return None

def remove_path_from_index(path):
    cur = get_current(path)

    if cur is None or cur.is_deleted():
        # path we wanted to remove from the index was missing
        # so we don't need to do anything
        return

    cur.set_deleted()
    cur.repack()
    if cur.nlink > 1 and not stat.S_ISDIR(cur.mode):
        _hlinks.del_path(cur.name)

def update_path_in_index(path, tstart):
    cur = get_current(path)

    if cur is None:
        # seems to be missing in the index, so add it
        add_path_to_index(path)

    try:
        pst = drecurse.OsFile(path).stat()

        meta = metadata.from_path(path, statinfo=pst)
        if not stat.S_ISDIR(cur.mode) and cur.nlink > 1:
            _hlinks.del_path(cur.name)
        if not stat.S_ISDIR(pst.st_mode) and pst.st_nlink > 1:
            _hlinks.add_path(path, pst.st_dev, pst.st_ino)
        # Clear these so they don't bloat the store -- they're
        # already in the index (since they vary a lot and they're
        # fixed length).  If you've noticed "tmax", you might
        # wonder why it's OK to do this, since that code may
        # adjust (mangle) the index mtime and ctime -- producing
        # fake values which must not end up in a .bupm.  However,
        # it looks like that shouldn't be possible:  (1) When
        # "save" validates the index entry, it always reads the
        # metadata from the filesytem. (2) Metadata is only
        # read/used from the index if hashvalid is true. (3) index
        # always invalidates "faked" entries, because "old != new"
        # in from_stat().
        meta.ctime = meta.mtime = meta.atime = 0
        meta_ofs = _msw.store(meta)
        cur.from_stat(pst, meta_ofs, tstart,
                          check_device=opt.check_device)
        cur.repack()
    except (OSError, IOError) as e:
        add_error(e)

def add_path_to_index(path):
    try:
        pst = drecurse.OsFile(path).stat()

        meta = metadata.from_path(path, statinfo=pst)

        # See same assignment to 0, above, for rationale.
        meta.atime = meta.mtime = meta.ctime = 0
        meta_ofs = _msw.store(meta)
        _wi.add(path, pst, meta_ofs)
        if not stat.S_ISDIR(pst.st_mode) and pst.st_nlink > 1:
            _hlinks.add_path(path, pst.st_dev, pst.st_ino)

    except (OSError, IOError) as e:
        add_error(e)

def update_watcher(path, excluded_paths, exclude_rxs):
    """
    Watch the path, and when any file or path changes,
    touch the file.
    """
    import inotify
    import select

    flag_set = {
            inotify.IN_CREATE,
            inotify.IN_DELETE,
            inotify.IN_MODIFY,
            inotify.IN_MOVED_FROM,
            inotify.IN_MOVED_TO,
            }

    mask = reduce(lambda x, y: x | y, flag_set)
    create_mask = inotify.IN_CREATE | inotify.IN_MOVED_TO
    delete_mask = inotify.IN_DELETE | inotify.IN_MOVED_FROM

    filters = set()
    if excluded_paths:
        filters.add(lambda path: path not in excluded_paths)
    if exclude_rxs:
        filters.add(lambda path: not should_rx_exclude_path(path, exclude_rxs))

    addfilter = None
    if filters:
        def addfilter(event):
            return all(filter(event.fullpath) for filter in filters)

    watcher = inotify.watcher.AutoWatcher(addfilter=addfilter)
    for realpath, path in paths:
        watcher.add_all(realpath, mask)

    if not watcher.watches():
        print('No files to watch')
        return

    poll = select.poll()
    poll.register(watcher, select.POLLIN)

    timeout = None

    threshold = inotify.watcher.Threshold(
            watcher,
            # read the watcher with every 256kB of inotify data
            # it is set this high reduce index merging during
            # IO-intensive tasks like compilation

            # TODO: make this configurable with an option for bup-watch
            256*1024,
            )

    def drecurse_cmp(left, right):
        """used to properly sort event tuples"""
        left, right = left[0], right[0]

        if left.startswith(right) and right.endswith(os.sep):
            return 1
        elif right.startswith(left) and left.endswith(os.sep):
            return -1
        else:
            return cmp(right, left)

    while True:
        events = poll.poll(timeout)

        read = False
        tdict = dict()

        if threshold() or not events:

            tmax = (time.time() - 1) * 10**9
            tstart = int(time.time()) * 10**9

            for event in watcher.read(False):
                read = True
                path = event.fullpath
                if event.mask & inotify.IN_ISDIR:
                    # bup internals expect a trailing slash for directories
                    path += os.sep
                flag = event.mask & mask # should have unique flag
                old_flag = tdict.get(path)
                if old_flag is not None:
                    # some special rules for overwriting flags
                    if old_flag & create_mask:
                        if flag & inotify.IN_MODIFY:
                            continue
                        elif flag & delete_mask:
                            del tdict[path]
                            continue
                    elif old_flag & delete_mask and flag & create_mask:
                        tdict[path] = inotify.IN_MODIFY
                        continue
                tdict[path] = flag

            if tdict: # if nothing changed, don't do anything
                setup_globals(tmax)
                for path, flag in sorted(tdict.items(), drecurse_cmp):
                    if flag & create_mask:
                        add_path_to_index(path)
                    elif flag & delete_mask:
                        remove_path_from_index(path)
                    else:
                        update_path_in_index(path, tstart)
                save_index(tmax)

        if read:
            timeout = None
            poll.register(watcher, select.POLLIN)
        else:
            # only merge every 10 seconds (unless you go over the data threshold)
            # TODO: make this configurable with an option
            timeout = 10000
            poll.unregister(watcher)


optspec = """
bup watch [options...] <filenames...>
--
 Options:
no-check-device don't invalidate an entry if the containing device changes
d,daemonize detach process from shell (requires the pidfile to be specified)
p,pidfile= pidfile for daemon
l,logfile= logfile for daemon (default: no log)
f,indexfile=  the name of the index file (normally BUP_DIR/bupindex)
exclude= a path to exclude from the backup (may be repeated)
exclude-from= skip --exclude paths in file (may be repeated)
exclude-rx= skip paths matching the unanchored regex (may be repeated)
exclude-rx-from= skip --exclude-rx patterns in file (may be repeated)
"""
o = options.Options(optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

# FIXME: remove this once we account for timestamp races, i.e. index;
# touch new-file; index.  It's possible for this to happen quickly
# enough that new-file ends up with the same timestamp as the first
# index, and then bup will ignore it.
tick_start = time.time()
time.sleep(1 - (tick_start - int(tick_start)))

git.check_repo_or_die()

indexfile = opt.indexfile or git.repo('bupindex')

handle_ctrl_c()

excluded_paths = parse_excludes(flags, o.fatal)
exclude_rxs = parse_rx_excludes(flags, o.fatal)
paths = index.reduce_paths(extra)

if not extra:
    o.fatal('watch requested but no paths given')

if opt.logfile:
    opt.logfile = os.path.realpath(opt.logfile)
else:
    opt.logfile = os.devnull

if opt.daemonize:
    if opt.pidfile:
        opt.pidfile = os.path.realpath(opt.pidfile)
    else:
        o.fatal('daemon requested but no pidfile specified')
    check_pidfile()
    daemonize()

update_watcher(paths, excluded_paths, exclude_rxs)
