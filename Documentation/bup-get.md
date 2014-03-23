% bup-get(1) Bup %BUP_VERSION%
% Rob Browning <rlb@defaultvalue.org>
% %BUP_DATE%

# NAME

bup-get - copy repository items

# SYNOPSIS

bup get \[-s *source-path*\] \[-r *host*:*path*\]  OPTIONS \<*item*...\>

# DESCRIPTION

`bup get` copies the indicated *item*s in the source repository to the
destination repository.  See the EXAMPLES below for a quick
introduction.

Each *item* must be in one of these formats:

    *src*[:*dest*]
    *src*::[*dest*][:*destopts*]

Here, *src* is the VFS path of the object to be fetched, and *dest* is
the optional destination.  Of course, tags may be refered to via the
VFS /.tag/ directory.  For example:

    bup get -s /source/repo /foo
    bup get -s /source/repo /foo/latest:/bar
    bup get -s /source/repo /foo/latest::/bar:+

Depending on the situation, *destopts* may contain one or more of
the following characters:

-----------------------------------------------------------------
opt meaning
--- -------------------------------------------------------------
 _  don't name the result (suppress any default destination)

 f  only fast-forward the destination, which must be a branch

 +  append to the destination, which must be a branch

 =  force the destination (overwrite a tag, clobber a branch)
-----------------------------------------------------------------

When dealing with branch destinations, the last three options indicate
the branch update "method", which defaults to "f".  A fast-forward
update requires that any existing destination be an ancestor of the
source.

An existing destination tag cannot be overwritten without force
(i.e. "f"), and an existing branch cannot be updated if it isn't a
fast-forward, unless "+" or "=" is specified.  The former will always
create a new commit (even if the branch could have been
fast-forwarded), and the latter will force the destination to be
identical to the source.

When appending (via "+"), each new destination commit will have the
same author and message as the original, but a committer and date
corresponding to the current user and time.

When *src* and *dest* name branches, an append will add the entire
source branch to the existing destination as a sequence of new
commits.

A tree may be appended to a destination branch via "+", which (among
other things), can use used to create a new branch containing only a
subtree from an existing branch.  See the EXAMPLES below.

When *src* names a tag or a branch, and no *dest* is given, bup will
use *src* as the destination name (unless "_" is specified).

When *src* names a save, and no *dest* is given, bup will use the
*src* branch name as the destination name (unless "_" is specified).

Some item specifications are invalid.  For example, "x::y:_" will be
rejected because it requests anonymity and specifies a destination
name, and "x::.tag/y:+" will be rejected because the branch option "+"
was specified for a tag destination.

For each destination reference updated, bup will print the commit,
tree, or tag hash, if requested by the appropriate options.  When
relevant, the tree hash will be printed before the commit hash.

Any anonymous *item*s may be left as dangling references until/unless
they're referred to some other way (cf. `bup tag`).

Local *item*s can be pushed to a remote repository with the `--remote`
option, and remote *item*s can be pulled into a local repository via
"bup on HOST get ...".  See `bup-on`(1) and the EXAMPLES below for
further information.

Assuming sufficient disk space (and until/unless bup supports
something like rm/gc), this command can be used to drop old, unwanted
backups by creating a new repository, fetching the desired saves into
it, and then deleting the old repository.

# OPTIONS

-s, \--source=*path*
:   use *path* as the source repository, instead of the default.

-r, \--remote=*host*:*path*
:   store the indicated items on the given remote server.  If *path*
    is omitted, uses the default path on the remote server (you still
    need to include the ':').  The connection to the remote server is
    made with SSH.  If you'd like to specify which port, user or
    private key to use for the SSH connection, we recommend you use
    the `~/.ssh/config` file.

-c, \--print-commits
:   for each updated branch, print the new git commit id.

-t, \--print-trees
:   for each updated branch, print the new git tree id of the
    filesystem root.

\--print-tags
:   for each updated tag, print the new git id.

-v, \--verbose
:   increase verbosity (can be used more than once).  With
    `-v`, print the name of every item fetched, with `-vv` add
    directory names, and with `-vvv` add every filename.

\--bwlimit=*bytes/sec*
:   don't transmit more than *bytes/sec* bytes per second to the
    server.  This can help avoid sucking up all your network
    bandwidth.  Use a suffix like k, M, or G to specify multiples of
    1024, 1024\*1024, 1024\*1024\*1024 respectively.
    
-*#*, \--compress=*#*
:   set the compression level to # (a value from 0-9, where
    9 is the highest and 0 is no compression).  The default
    is 1 (fast, loose compression)

# EXAMPLES

    # Update or copy the archives branch in src-repo to the local repository.
    $ bup get -s src-repo archives

    # Append a particular archives save to the pruned-archives branch.
    $ bup get -s src-repo archives/2013-01-01-030405:pruned-archives

    # Update or copy the archives branch on remotehost to the local
    # repository.
    $ bup on remotehost get archives

    # Update or copy the local branch archives to remotehost.
    $ bup get -r remotehost: archives

    # Update or copy the archives branch in src-repo to remotehost.
    $ bup get -s src-repo -r remotehost: archives

    # Append the local archives branch to the archives-2 branch on
    # remotehost.  If archives-2 doesn't exist, or is an earlier
    # version of archives, the resulting branches will be identical.
    $ bup get -r remotehost: -n archives:archives-2

    # Replace the unwanted branch with the better branch (both local).
    $ bup get better::unwanted:=

    # Copy the latest local save from the archives branch to the
    # remote tag foo.
    $ bup get -r remotehost: archives/latest::.tag/foo

    # Append foo (from above) to the local other-archives branch.
    $ bup on remotehost get .tag/foo::other-archives:+

    # Create a new home branch contaning only /home from archives/latest.
    $ bup get -s "$BUP_DIR" archives/latest/home::home:+

# SEE ALSO

`bup-on`(1), `bup-tag`(1), `ssh_config`(5)

# BUP

Part of the `bup`(1) suite.
