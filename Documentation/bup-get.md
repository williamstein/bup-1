% bup-get(1) Bup %BUP_VERSION%
% Rob Browning <rlb@defaultvalue.org>
% %BUP_DATE%

# NAME

bup-get - copy items from one repository to another

# SYNOPSIS

bup get [-s *source-path*] [-r *host*:*path*] \<-t|-c\> [-#] [-v] \<*item*...\>

bup get -n *name* [-s *source-path*] [-r *host*:*path*] [-t] [-c] [-#] [-v]
\<*treeish*...\>

# DESCRIPTION

`bup get` copies the named objects in the source repository to the
destination repository.  When resolving a name, VFS paths take
precedence over tags and hashes.

When a branch *name* is not specified, bup will fetch each *item* into
the destination repository.  If the *item* is a branch name, then the
branch with that name in the destination repository will be updated to
match the source.  For each branch updated, bup will print the
`--tree` and `--commit` if requested.  Note that nothing will be
printed for other *item*s, which may leave them as dangling references
until/unless they're referred to in the repository some other way
(cf. `bup tag`).

When a branch *name* is specified, each *treeish* object must resolve
to either a VFS branch, VFS save, commit, or tree, and the resulting
tree (or in the case of a branch, set of trees) will be appended to
*name*.

Whenever the destination is a branch, bup will fast-forward it if
possible (i.e. whenever the destination is an ancestor of the source).
When not fast-forwarding, each new destination commit will have the
same author and message as the original, but will have a committer and
date corresponding to the current user and time.  By default, bup will
refuse non-fast-forward updates unless `--force` is specified.

You can push local items to a remote repository with the `--remote`
option, and you can pull remote items into a local repository via "bup
on HOST get ...".  See `bup-on`(1) and the EXAMPLES below for further
information.

Assuming you have sufficient disk space (and until/unless bup supports
something like rm/gc), this command can be used to drop old, unwanted
backups by creating a new repository, fetching the saves that you want
to keep into it, and then deleting the old repository.

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

-t, \--tree
:   for each relevant commit, print the git tree id of the filesystem
    root.
    
-c, \--commit
:   for each relevant branch (see above), print the new git commit id.

-n, \--name=*name*
:   append each retrieved item it to the git branch named *name* as a
    new commit (or fast-forward).  If *name* doesn't exist, create it.

\--force
:   update the destination branch (even if it isn't an ancestor of the
    source branch) by appending a new commit to the destination for
    every commit in the source (by default bup will refuse).  In git
    parlance, this option causes bup to proceed even when the
    destination can't be fast-forwarded to match the source.

-v, \--verbose
:   increase verbosity (can be used more than once).  With
    `-v`, print the name of every item fetched, with `-vv` add
    directory names, and with `-vvv` add every filename.

\--bwlimit=*bytes/sec*
:   don't transmit more than *bytes/sec* bytes per second
    to the server.  This is good for making your backups
    not suck up all your network bandwidth.  Use a suffix
    like k, M, or G to specify multiples of 1024,
    1024*1024, 1024*1024*1024 respectively.
    
-*#*, \--compress=*#*
:   set the compression level to # (a value from 0-9, where
    9 is the highest and 0 is no compression).  The default
    is 1 (fast, loose compression)

# EXAMPLE

    # Update or copy the archives branch in src-repo to the local repository.
    $ bup get -s src-repo archives

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
    # Otherwise, bup will append archive to archive-2 as new commits
    # to bring it up to date.
    $ bup get -r remotehost: -n archives-2 archives

    # Append src-repo archives/latest to the local archives-2 branch.
    $ bup get -s src-repo -n archives-2 archives/latest

# SEE ALSO

`bup-on`(1), `ssh_config`(5)

# BUP

Part of the `bup`(1) suite.
