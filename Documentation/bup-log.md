% bup-log(1) Bup %BUP_VERSION%
% Holger Dell <github@holgerdell.com>
% %BUP_DATE%

# NAME

bup-log - Inspect bup's backup history (EXPERIMENTAL)

# SYNOPSIS

bup log [--ref=<*ref*>] [options] [<*path*>]

# DESCRIPTION

`bup log` shows bup logs.
Without arguments, it will list all saves on any branch; for each branch the output will be sorted from most recent to oldest.
If *path* is given, it will only list saves in which a file in *path* was
changed when compared to the preceding save.
If *path* is a directory, it will consider all changes made to any file in
the directory or any of its subdirectories.

# OPTIONS

\-n <*number*>, --max-count=<*number*>
:   Limit the number of saves to output.

\--since=<*date*>, --after=<*date*>
:   Show saves more recent that a given *date*.
    Example:

        $ bup log --since=yesterday

\--until=<*data*>, --before=<*date*>
:   Show saves older that a given *date*.
    Example:

        $ bup log --after="2013-01-01" --before="2014-01-01"

\--sparse
:   (EXPERIMENTAL)
    Only display a sparse subset of saves; fewer the further past they are.
    The approximate frequencies are:

    - in the last day, at most one save per 15 minutes
    - in the last three days, at most one save per hour
    - in the last week, at most one save per day
    - in the last year, at most one save per month
    - beyond that, at most one save per year

    `--sparse` always shows the most recent and the oldest saves (unless
    `--max-count` cuts them off).

\--ref=<*ref*>
:   Only display commits reachable from ref (branch, commit, or tag).

\--format=<*format*>
:   Change the way each commit is displayed.
    The default format is '%H %Cgreen%ar%Creset', where %H is the
    commit hash and %ar is a human-friendly date format.
    For a full list of format options, see git help log.

\--shortstat
:   In addition to the things listed in `--format`, also print the number of
    files that have changed. Note that computing this number can take a very
    long time as it does not currently get cached.

\--changes
:   In addition to the things listed in `--format`, also print the path of all
    files that were actually changed.
    Note that *all* files are considered to be 'changed' in the very first
    save.

\--reverse
:   By default, saves are listed from most recent to oldest. With this option,
    the order is reversed before the list is printed. Note that filters such as
    `--since`, `--until`, and `--sparse` are applied *before* the list is
    reversed, but the `--max-count` option is processed *after* the list is
    reversed.

# EXAMPLES

Show all changes affecting files in \$HOME/Documents, and show which files were changed:

    $ bup log --changes $HOME/Documents

Show all files that were changed in the second to most recent backup on mybranch:

    $ bup log --ref=mybranch^ --max-count=1 --changes

Show all saves in which an important document was modified, and retrieve a backup of the file:

    $ bup log $HOME/Important.txt
        c0f269d405ca59eba2dac9f1cf97ddcf20752f61 23 minutes ago
        f549d72c65a82967ec52b364251b4f69fb39a46c 1 hour ago
        66e226d5ad02dbd32f105133b7d145c1e41cf6b3 7 hours ago

    $ bup cat-file .commits/66/e226d5ad02dbd32f105133b7d145c1e41cf6b3/$HOME/Important.txt

# SEE ALSO

`bup-cat-file`(1)

# BUP

Part of the `bup`(1) suite.
