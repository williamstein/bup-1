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
    if opt.verbose >= 2:
        print('removing {0} from the index'.format(path))

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
    if opt.verbose >= 2:
        print('updating {0} in the index'.format(path))

    cur = get_current(path)

    if cur is None:
        # seems to be missing in the index, so add it
        return add_path_to_index(path)

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

def add_path_to_index(path):
    if opt.verbose >= 2:
        print('adding {0} to the index'.format(path))

    pst = drecurse.OsFile(path).stat()

    meta = metadata.from_path(path, statinfo=pst)

    # See same assignment to 0, above, for rationale.
    meta.atime = meta.mtime = meta.ctime = 0
    meta_ofs = _msw.store(meta)
    _wi.add(path, pst, meta_ofs)
    if not stat.S_ISDIR(pst.st_mode) and pst.st_nlink > 1:
        _hlinks.add_path(path, pst.st_dev, pst.st_ino)

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
    submask = mask | inotify.IN_ONLYDIR
    create_mask = inotify.IN_CREATE | inotify.IN_MOVED_TO
    delete_mask = inotify.IN_DELETE | inotify.IN_MOVED_FROM

    filters = set()
    if excluded_paths:
        filters.add(lambda path: path not in excluded_paths)
    if exclude_rxs:
        filters.add(lambda path: not should_rx_exclude_path(path, exclude_rxs))
    if opt.xdev:
        xdevs = []
        for realpath, path in paths:
            xdevs.append((realpath, drecurse.OsFile(path).stat().st_dev))
        def xdev_check(path):
            try:
                path = os.path.realpath(path)
                st = drecurse.OsFile(os.path.realpath(path)).stat()
                if stat.S_ISDIR(st.st_mode):
                    path += os.sep
                xdev = st.st_dev
                return any(path.startswith(dpath) and xdev == dxdev for dpath, dxdev in xdevs)
            except OSError as err:
                # race condition
                if err.errno == errno.ENOENT:
                    return False
        filters.add(xdev_check)

    def addfilter(path):
        return all(filter(path) for filter in filters)

    watcher = inotify.watcher.AutoWatcher(addfilter=lambda event: addfilter(event.fullpath))

    def recursive_add_to_watcher(base):
        try:
            for path in (os.path.join(base, path) for path in os.listdir(base)):
                if os.path.isdir(path) and not os.path.islink(path) and addfilter(path):
                    recursive_add_to_watcher(path)
        except OSError:
            return
        watcher.add(base, submask)

    for realpath, path in paths:
        recursive_add_to_watcher(realpath)

    if not watcher.watches():
        print('No files to watch')
        return

    poll = select.poll()
    poll.register(watcher, select.POLLIN)

    timeout = None

    threshold = inotify.watcher.Threshold(
            watcher,
            opt.buffer_size,
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

    read = False
    tdict = dict()

    while True:
        events = poll.poll(timeout)

        if threshold() or not events:

            tmax = (time.time() - 1) * 10**9
            tstart = int(time.time()) * 10**9

            for event in watcher.read(False):
                path = event.fullpath
                if path is None:
                    continue
                read = True
                path = os.path.realpath(path)
                cur_mask = event.mask & mask
                old_mask = tdict.get(path, (None,))[0]
                if old_mask is not None:
                    # some special rules for overwriting flags
                    if old_mask & create_mask:
                        if cur_mask & inotify.IN_MODIFY:
                            continue
                        elif cur_mask & delete_mask:
                            del tdict[path]
                            continue
                    elif old_mask & delete_mask and cur_mask & create_mask:
                        cur_mask = (cur_mask & ~create_mask) | inotify.IN_MODIFY
                tdict[path] = (cur_mask, tstart)

            if tdict: # if nothing changed, don't do anything
                new_tdict = dict()
                setup_globals(tmax)
                for path, (mask, etime) in sorted(tdict.items(), drecurse_cmp):
                    if not addfilter(path):
                        continue

                    if mask & inotify.IN_ISDIR:
                        # bup internals expect a trailing slash for directories
                        bup_path += os.sep
                    else:
                        bup_path = path

                    try:
                        if mask & create_mask:
                            add_path_to_index(bup_path)
                        elif mask & delete_mask:
                            remove_path_from_index(bup_path)
                        else:
                            update_path_in_index(bup_path, tmax)
                    except (OSError, IOError, Exception) as err:
                        # these are for race conditions
                        #  - OSError and IOError should be obvious
                        #  - the blanket Exception is for some concurency
                        #    issues in the bupindex code
                        if etime +  60*10**9 <= tstart:
                            # if it takes too long to resolve a supposed
                            # race condition, it is probably a real error
                            add_error(err)
                        else:
                            new_tdict[path] = (mask, etime)

                tdict = new_tdict
                save_index(tmax)

        if read:
            read = False
            timeout = None
            poll.register(watcher, select.POLLIN)
        else:
            # record changes at most every buffer_time
            timeout = opt.save_interval
            poll.unregister(watcher)


optspec = """
bup watch <--start|stop|restart> [-p pidfile] <filenames...>
--
 Modes:
start                   start daemon
stop                    stop daemon
restart                 restart daemon (default)
 Options:
no-detach               don't detach process from shell (i.e. don't run as a deamon)
p,pidfile=              pidfile for daemon (required)
l,logfile=              logfile for daemon (default: no log)
buffer-size=            size (in bytes) of the buffer (default: 32kB)
save-interval=          time (in ms) between saves to the bupindex (default: 10s)
no-check-device         don't invalidate an entry if the containing device changes
f,indexfile=            the name of the index file (normally BUP_DIR/bupindex)
exclude=                a path to exclude from the backup (may be repeated)
exclude-from=           skip --exclude paths in file (may be repeated)
exclude-rx=             skip paths matching the unanchored regex (may be repeated)
exclude-rx-from=        skip --exclude-rx patterns in file (may be repeated)
v,verbose               increase log output (can be used more than once)
x,xdev,one-file-system  don't cross filesystem boundaries
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

if opt.logfile:
    opt.logfile = os.path.realpath(opt.logfile)
else:
    opt.logfile = os.devnull

if not (opt.start or opt.stop or opt.restart):
    opt.restart = True

if opt.restart:
    opt.start = opt.stop = opt.restart

if opt.detach:
    if opt.pidfile:
        opt.pidfile = os.path.realpath(opt.pidfile)
    else:
        o.fatal('daemon requested but no pidfile specified')

    if opt.stop:
        kill_daemon(opt.restart)

    if opt.start:
        check_pidfile()
        daemonize()
    else:
        sys.exit()

if not extra:
    o.fatal('watch requested but no paths given')

excluded_paths = parse_excludes(flags, o.fatal)
exclude_rxs = parse_rx_excludes(flags, o.fatal)
paths = index.reduce_paths(extra)

if not opt.buffer_size:
    opt.buffer_size = 32*1024
if not opt.save_interval:
    opt.save_interval = 10000

update_watcher(paths, excluded_paths, exclude_rxs)
