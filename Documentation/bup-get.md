% bup-get(1) Bup %BUP_VERSION%
% Rob Browning <rlb@defaultvalue.org>
% %BUP_DATE%

# NAME

bup-get - add items (recursively) to the current repository

# SYNOPSIS

bup get [-r *host*:*path*] \<-t|-c|-n *name*\> [-#] [-v]
*source-repo-path* \<(*hash* | *save*)...\>

# DESCRIPTION

`bup get` fetches the items named *hash* or *save* in the repository
specified by *source-repo-path* into the current repository.  A *save*
must be of the form /_branch_/_revision_, and must represent a valid
`bup save` as reported by `bup ls /_branch_` in the source repository.
If an item refers to a tree or a commit, its content will be retrieved
recursively.

Note that in order to refer to the items that were fetched later, you
must either specify `--name` (the normal case), or record the tree or
commit id printed by `--tree` or `--commit`.  When both options are
specified, the tree id will be printed before the commit id for each
item fetched.

When `--name` is specified, any /_branch_/_revision_ items that are
retrieved will be appended to the indicated backup set, and the
resulting commit will have the same author and message as the
original, but will have a "committer" and date corresponding to the
current user and time.

Assuming you have sufficient disk space (and until/unless bup supports
something like rm/gc), this command can be used to drop old, unwanted
backups by creating a new repository, fetching the saves that you want
to keep into it, and then deleting the old repository.

# OPTIONS

-r, \--remote=*host*:*path*
:   store the indicated items on the given remote server.  If *path*
    is omitted, uses the default path on the remote server (you still
    need to include the ':').  The connection to the remote server is
    made with SSH.  If you'd like to specify which port, user or
    private key to use for the SSH connection, we recommend you use
    the `~/.ssh/config` file.

-t, \--tree
:   after retrieving a save:, print the git tree id of the filesystem
    root.
    
-c, \--commit
:   after making a commit for a retrieved save: (when `--name` or
    `--commit` is specified) , print the new git commit id.

-n, \--name=*name*
:   after retrieving a save:, append it to the git branch named *name*
    as a new commit.  If *name* doesn't exist, create it.

-v, \--verbose
:   increase verbosity (can be used more than once).  With
    one -v, prints every directory name as it gets backed up.  With
    two -v, also prints every filename.

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

    # Append the local lib/latest save to the remote-lib branch on remotehost.
    $ bup get -r remotehost: -n remote-lib --bwlimit=50k save:lib/latest

    # Append save from /other/repo to the lib branch in the default repository.
    $ bup get -n lib -s /other/repo save:lib/latest

    # Append the latest save on the remoteserver's remotelib branch to
    # the lib branch in the local default repository.
    $ bup on remotehost get -n lib save:remotelib/latest

    # Append the latest save on the remotelib branch in /some/repo on
    # remoteserver to the lib branch in the local default repository.
    $ bup on remotehost get -n lib -s /some/repo save:remotelib/latest

# SEE ALSO

`bup-on`(1), `ssh_config`(5)

# BUP

Part of the `bup`(1) suite.
