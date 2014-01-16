#!/usr/bin/env bash
. ./wvtest-bup.sh

set -o pipefail

top="$(WVPASS pwd)" || exit $?
tmpdir="$(WVPASS wvmktempdir)" || exit $?

export BUP_DIR="$tmpdir/bup"
export GIT_DIR="$tmpdir/bup"

bup() { "$top/bup" "$@"; }

WVPASS bup init
WVPASS cd "$tmpdir"
WVPASS mkdir src

WVSTART "sparse file restore (all sparse)"
WVPASS dd if=/dev/zero of=src/foo seek=1M bs=1 count=1
WVPASS bup index src
WVPASS bup save -n src src
WVPASS bup restore -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -gt 1000 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --no-sparse (all sparse)"
WVPASS rm -r restore
WVPASS bup restore --no-sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -gt 1000 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (all sparse)"
WVPASS dd if=/dev/zero of=src/foo seek=1M bs=1 count=1
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -lt 100 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (sparse end)"
WVPASS echo "start" > src/foo
WVPASS dd if=/dev/zero of=src/foo seek=1M bs=1 count=1 conv=notrunc
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -lt 100 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (sparse middle)"
WVPASS echo "end" >> src/foo
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -lt 100 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (sparse start)"
WVPASS dd if=/dev/zero of=src/foo seek=1M bs=1 count=1
WVPASS echo "end" >> src/foo
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -lt 100 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (sparse start and end)"
WVPASS dd if=/dev/zero of=src/foo seek=1M bs=1 count=1
WVPASS echo "middle" >> src/foo
WVPASS dd if=/dev/zero of=src/foo seek=2M bs=1 count=1 conv=notrunc
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
restore_size=$(WVPASS du -k -s restore | WVPASS cut -f1) || exit $?
WVPASS [ "$restore_size" -lt 100 ]
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVSTART "sparse file restore --sparse (random)"
WVPASS bup random 512k > src/foo
WVPASS bup index src
WVPASS bup save -n src src
WVPASS rm -r restore
WVPASS bup restore --sparse -C restore "src/latest/$(pwd)/"
WVPASS "$top/t/compare-trees" -c src/ restore/src/

WVPASS rm -rf "$tmpdir"
