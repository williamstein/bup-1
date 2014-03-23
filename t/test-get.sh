#!/usr/bin/env bash
. ./wvtest-bup.sh
. ./t/lib.sh

set -o pipefail

top="$(WVPASS pwd)" || exit $?
tmpdir="$(WVPASS wvmktempdir)" || exit $?


bup() { "$top/bup" "$@"; }


reset-bup-dest()
{
    export BUP_DIR=get-dest
    WVPASS rm -rf "$BUP_DIR"
    WVPASS bup init
}


validate-blob()
{
    local src_item="$1"
    local dest_item="$2"
    WVPASSEQ "$(git --git-dir get-src cat-file blob "$src_item")" \
        "$(git --git-dir get-dest cat-file blob "$dest_item")"
}


validate-tree()
{
    local src_id="$1"
    local dest_id="$2"
    WVPASS force-delete restore-src restore-dest
    WVPASS mkdir restore-src restore-dest
    # Tag the trees so the archive contents will have matching timestamps.
    GIT_COMMITTER_DATE="2014-01-01 01:01 CST" \
        WVPASS git --git-dir get-src tag -am '' tmp-src "$src_id"
    GIT_COMMITTER_DATE="2014-01-01 01:01 CST" \
        WVPASS git --git-dir get-dest tag -am '' tmp-dest "$dest_id"
    WVPASS git --git-dir get-src archive tmp-src | tar xf - -C restore-src
    WVPASS git --git-dir get-dest archive tmp-dest | tar xf - -C restore-dest
    WVPASS WVPASS git --git-dir get-src tag -d tmp-src
    WVPASS WVPASS git --git-dir get-dest tag -d tmp-dest
    diff -ruN restore-src restore-dest
    WVPASS "$top/t/compare-trees" -c restore-src/ restore-dest/
    WVPASS force-delete restore-src restore-dest
}


validate-save()
{
    local orig_dir="$1"
    local save_path="$2"
    local get_log="$3"
    local commit_id="$4"
    local tree_id="$5"

    WVPASS rm -rf restore
    WVPASS bup restore -C restore "$save_path/."
    diff -ruN "$orig_dir" restore
    find "$orig_dir"
    find restore
    WVPASS "$top/t/compare-trees" -c "$orig_dir/" restore/
    local orig_git_dir="$GIT_DIR"
    export GIT_DIR="$BUP_DIR"
    if test "$tree_id"; then
        WVPASS git ls-tree "$tree_id"
        WVPASS git cat-file commit "$commit_id" | head -n 1 \
            | WVPASS grep -q "^tree $tree_id\$"
    fi
    if test "$orig_git_dir"; then
        export GIT_DIR="$orig_git_dir"
    else
        unset GIT_DIR
    fi
}


given_count=0
given()
{
    # given() is the core of the bup get testing infrastructure, it
    # handles calls like this:
    #
    #   WVPASS given src-branch \
    #     get save/latest::src-branch" \
    #     produces save obj "$(pwd)/src" "$commit_id" "$tree_id" \
    #     matching ./src-2 \
    #     only-heads src-branch
    #     only-tags ''

    # FIXME: eventually have "fails" test that there was *no* effect
    # on the dest repo?
    ((given_count++))
    if test "$#" -lt 4; then
        echo "error: too few arguments to given" 1>&2
        exit 1
    fi

    local existing_dest="$1"
    local get="$2"
    local item="$3"
    local expectation="$4"
    local get_cmd
    shift 4 # Remaining arguments handled later.

    if test "$get" = get; then
        get_cmd="bup get -vvct --print-tags -s get-src"
    elif test "$get" = get-on; then
        get_cmd="bup on - get -vvct --print-tags -s get-src"
    elif test "$get" = get-to; then
        get_cmd="bup get -vvct --print-tags -s get-src -r -:$(pwd)/get-dest"
    else
        echo "error: unexpected get type $get" 1>&2
        exit 1
    fi

    WVPASS reset-bup-dest
    if test "$existing_dest" != nothing; then
        WVPASS $get_cmd -vct --print-tags "$existing_dest"
    fi

    if test "$expectation" = fails; then
        $get_cmd "$item"
        local rc=$?
        WVPASS test $rc -eq 97 -o $rc -eq 98
    elif test "$expectation" = produces; then
        if test "$#" -lt 1; then
            echo "error: too few arguments to produces" 1>&2
            exit 1
        fi
        WVPASS $get_cmd "$item" | tee get.log
        while test $# -ne 0; do
            local requirement="$1"
            shift
            case "$requirement" in
                tag)
                    if test "$#" -lt 1; then
                        echo "error: \"produces tag\" requires a name" 1>&2
                        exit 1
                    fi
                    local tmp_name="$1"; shift
                    WVPASS git --git-dir get-dest show-ref --tags "$tmp_name"
                    ;;
                head)
                    if test "$#" -lt 1; then
                        echo "error: \"produces head\" requires a name" 1>&2
                        exit 1
                    fi
                    local tmp_name="$1"; shift
                    WVPASS git --git-dir get-dest show-ref --heads "$tmp_name"
                    ;;
                only-tags)
                    if test "$#" -lt 1; then
                        echo "error: \"produces only-tags\" requires a list of tags" 1>&2
                        exit 1
                    fi
                    local tmp_names="$1"; shift
                    local tmp_name
                    find get-dest/refs
                    for tmp_name in $tmp_names; do
                        WVPASS git --git-dir get-dest show-ref --tags "$tmp_name"
                    done
                    local tmp_n="$(echo $tmp_names | tr ' ' '\n' | sort -u | wc -w)" || exit $?
                    WVPASSEQ "$tmp_n" "$(git --git-dir get-dest show-ref -s --tags "$tmp_name" | wc -w)"
                    ;;

                only-heads)
                    if test "$#" -lt 1; then
                        echo "error: \"produces only-heads\" requires a list of heads" 1>&2
                        exit 1
                    fi
                    local tmp_names="$1"; shift
                    local tmp_name
                    for tmp_name in $tmp_names; do
                        WVPASS git --git-dir get-dest show-ref --heads "$tmp_name"
                    done
                    local tmp_n="$(echo $tmp_names | tr ' ' '\n' | sort -u | wc -w)" || exit $?
                    WVPASSEQ "$tmp_n" "$(git --git-dir get-dest show-ref -s --heads "$tmp_name" | wc -w)"
                    ;;
                blob|tree|commit)
                    if test "$#" -lt 3; then
                        echo "error: too few arguments to \"produces blob\"" 1>&2
                        exit 1
                    fi
                    local dest_name="$1"
                    local comparison="$2"
                    local orig_value="$3"
                    shift 3
                    if test "$comparison" != matching; then
                        WVDIE "error: unrecognized comparison type \"$comparison\""
                        exit 1
                    fi
                    validate-"$requirement" "$orig_value" "$dest_name"
                    ;;
                save)
                    if test "$#" -lt 5; then
                        echo "error: too few arguments to \"produces save\"" 1>&2
                        exit 1
                    fi
                    local dest_name="$1"
                    local restore_subpath="$2"
                    local commit_id="$3"
                    local tree_id="$4"
                    local comparison="$5"
                    local orig_value="$6"
                    shift 6
                    if test "$comparison" != matching; then
                        # FIXME: use everywhere?
                        WVDIE "error: unrecognized comparison type \"$comparison\""
                        exit 1
                    fi
                    WVPASSEQ "$(cat get.log | wc -l)" 2
                    local get_tree_id=$(WVPASS awk 'FNR == 1' get.log) || exit $?
                    local get_commit_id=$(WVPASS awk 'FNR == 2' get.log) || exit $?
                    WVPASSEQ "$commit_id" "$get_commit_id"
                    WVPASSEQ "$tree_id" "$get_tree_id"
                    validate-save "$orig_value" "$dest_name$restore_subpath" get.log "$commit_id" "$tree_id" 
                    ;;
                new-save)
                    if test "$#" -lt 5; then
                        echo "error: too few arguments to \"produces new-save\"" 1>&2
                        exit 1
                    fi
                    local dest_name="$1"
                    local restore_subpath="$2"
                    local commit_id="$3"
                    local tree_id="$4"
                    local comparison="$5"
                    local orig_value="$6"
                    shift 6
                    if test "$comparison" != matching; then
                        # FIXME: use everywhere?
                        WVDIE "error: unrecognized comparison type \"$comparison\""
                        exit 1
                    fi
                    WVPASSEQ "$(cat get.log | wc -l)" 2
                    local get_tree_id=$(WVPASS awk 'FNR == 1' get.log) || exit $?
                    local get_commit_id=$(WVPASS awk 'FNR == 2' get.log) || exit $?
                    WVPASSNE "$commit_id" "$get_commit_id"
                    WVPASSEQ "$tree_id" "$get_tree_id"
                    validate-save "$orig_value" "$dest_name$restore_subpath" get.log "$get_commit_id" "$tree_id" 
                    ;;
                tagged-save)
                    if test "$#" -lt 5; then
                        echo "error: too few arguments to \"produces save\"" 1>&2
                        exit 1
                    fi
                    local tag_name="$1"
                    local restore_subpath="$2"
                    local commit_id="$3"
                    local tree_id="$4"
                    local comparison="$5"
                    local orig_value="$6"
                    shift 6
                    if test "$comparison" != matching; then
                        # FIXME: use everywhere?
                        WVDIE "error: unrecognized comparison type \"$comparison\""
                        exit 1
                    fi
                    WVPASSEQ "$(cat get.log | wc -l)" 1
                    local get_tag_id=$(WVPASS awk 'FNR == 1' get.log) || exit $?
                    WVPASSEQ "$commit_id" "$get_tag_id"
                    # Make sure tmp doesn't already exist.
                    WVFAIL git --git-dir get-dest show-ref tmp-branch-for-tag
                    find get-dest/refs
                    WVPASS git --git-dir get-dest branch tmp-branch-for-tag \
                        "refs/tags/$tag_name"
                    validate-save "$orig_value" \
                        "tmp-branch-for-tag/latest$restore_subpath" get.log \
                        "$commit_id" "$tree_id"
                    WVPASS git --git-dir get-dest branch -D tmp-branch-for-tag
                    ;;
                *)
                    WVDIE "error: unrecognized produces clause \"$requirement $*\""
                    exit 1
                    ;;
            esac
        done
        WVPASS rm get.log
    else
        WVDIE "error: unrecognized expectation \"$expectation $@\""
        exit 1
    fi
    return 0
}


test-item-to-root()
{
    local get="$1"
    local type="$2"
    local item="$3"
    WVSTART "given nothing $get $type $source_desc to /"
    WVPASS given nothing "$get" "$item::/" fails
    WVPASS given nothing "$get" "$item::/:+" fails
    WVPASS given nothing "$get" "$item::/:=" fails
    WVPASS given nothing "$get" "$item::/:f" fails
    WVPASS given nothing "$get" "$item::/:_" fails
    WVPASS given nothing "$get" "$item::/:_+" fails
    WVPASS given nothing "$get" "$item::/:_=" fails
    WVPASS given nothing "$get" "$item::/:_f" fails
}


test-blob-src()
{
    local get="$1"
    local item="$2"
    local item_id="$3"

    local tagged_src ref_name
    if test "${item:0:5}" = .tag/; then
        ref_name="${item:5}"
        tagged_src=true
        source_desc=tag
    else
        ref_name="$item"
        source_desc=path
    fi

    WVSTART "given nothing, $get blob $source_desc"
    WVPASS given nothing "$get" "$item:::+" fails
    WVPASS given nothing "$get" "$item:::f" fails
    WVPASS given nothing "$get" "$item:::_+" fails
    WVPASS given nothing "$get" "$item:::_=" fails
    WVPASS given nothing "$get" "$item:::_f" fails
    WVPASS given nothing "$get" "$item:branch" fails
    WVPASS given nothing "$get" "$item::branch" fails
    WVPASS given nothing "$get" "$item::branch:+" fails
    WVPASS given nothing "$get" "$item::branch:=" fails
    WVPASS given nothing "$get" "$item::branch:f" fails
    WVPASS given nothing "$get" "$item::branch:_" fails
    WVPASS given nothing "$get" "$item::branch:_+" fails
    WVPASS given nothing "$get" "$item::branch:_=" fails
    WVPASS given nothing "$get" "$item::branch:_f" fails
    for spec in "$item" "$item::" "$item:::="; do
        if test "$tagged_src"; then
            WVPASS given nothing "$get" "$item" \
                produces blob "$item_id" matching "$item_id" \
                only-heads '' only-tags "$ref_name"
        else  # Not tag source.
            WVPASS given nothing "$get" "$item" \
                produces blob "$item_id" matching "$item_id" \
                only-heads '' only-tags ''
        fi
    done

    WVPASS given nothing "$get" "$item:::_" \
        produces blob "$item_id" matching "$item_id" \
        only-heads '' only-tags ''
    WVPASS given nothing "$get" "$item:.tag/dest" \
        produces blob dest matching "$item_id" \
        only-heads '' only-tags ''

    declare -a existing_types=("blob tag" "tree tag" "commit tag" "commit head")
    declare -a existing_ids=( \
        .tag/tinyfile::.tag/obj .tag/tree-1::.tag/obj .tag/commit-2::.tag/obj \
        .tag/commit-2::obj)
    for ((i=0; i < ${#existing_types[@]}; i++)); do
        local existing_type="${existing_types[$i]}"
        local existing_id="${existing_ids[$i]}"

        WVSTART "given $existing_type, $get blob $source_desc"
        if [[ "$existing_type" =~ tag ]]; then
            WVPASS given "$existing_id" "$get" "$item::.tag/obj" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:=" \
                produces blob "$item_id" matching "$item_id" \
                only-heads '' only-tags obj
        else
            WVPASS given "$existing_id" "$get" "$item::.tag/obj" \
                produces blob "$item_id" matching "$item_id" \
                only-heads obj only-tags obj
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:=" \
                produces blob "$item_id" matching "$item_id" \
                only-heads obj only-tags obj
        fi
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:+" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:f" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_+" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_=" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_f" fails
    done

    test-item-to-root "$get" blob "$item"
}


# FIXME: check funcs for src:dest and src::dest cases.

test-tree-src()
{
    local get="$1"
    local item="$2"
    local item_id="$3"
    local src_dir="$4"

    local type=tree
    local tagged_src ref_name
    if test "${item:0:5}" = .tag/; then
        ref_name="${item:5}"
        tagged_src=true
        source_desc=tag
    else
        ref_name="$item"
        source_desc=path
    fi

    WVSTART "given nothing, $get $type $source_desc"
    WVPASS given nothing "$get" "$item:::+" fails
    WVPASS given nothing "$get" "$item:::f" fails
    WVPASS given nothing "$get" "$item:::_+" fails
    WVPASS given nothing "$get" "$item:::_=" fails
    WVPASS given nothing "$get" "$item:::_f" fails
    WVPASS given nothing "$get" "$item:branch" fails
    WVPASS given nothing "$get" "$item::branch" fails
    WVPASS given nothing "$get" "$item::branch:=" fails
    WVPASS given nothing "$get" "$item::branch:f" fails
    WVPASS given nothing "$get" "$item::branch:_" fails
    WVPASS given nothing "$get" "$item::branch:_+" fails
    WVPASS given nothing "$get" "$item::branch:_=" fails
    WVPASS given nothing "$get" "$item::branch:_f" fails

    # Since we don't have a commit that we want to ensure changes,
    # just use "$tree_id" as the commit id.
    WVPASS given nothing "$get" "$item::branch:+" \
        produces new-save branch/latest '' "$item_id" "$item_id" \
        matching "$src_dir" \
        only-heads branch only-tags ''

    for spec in "$item" "$item::" "$item:::="; do
        if test "$tagged_src"; then
            WVPASS given nothing "$get" "$item" \
                produces "$type" "$item_id" matching "$item_id" \
                only-heads '' only-tags "$ref_name"
        else  # Not a tag source.
            WVPASS given nothing "$get" "$item" \
                produces "$type" "$item_id" matching "$item_id" \
                only-heads '' only-tags ''
        fi
    done

    WVPASS given nothing "$get" "$item:::_" \
        produces "$type" "$item_id" matching "$item_id" \
        only-heads '' only-tags ''
    WVPASS given nothing "$get" "$item:.tag/dest" \
        produces "$type" dest matching "$item_id" \
        only-heads '' only-tags dest

    declare -a existing_types=("blob tag" "tree tag" "commit tag" "commit head")
    declare -a existing_ids=( \
        .tag/tinyfile::.tag/obj .tag/tree-1::.tag/obj .tag/commit-2::.tag/obj \
        .tag/commit-2::obj)
    for ((i=0; i < ${#existing_types[@]}; i++)); do
        local existing_type="${existing_types[$i]}"
        local existing_id="${existing_ids[$i]}"

        WVSTART "given $existing_type, $get $type $source_desc"
        WVPASS given "$existing_id" "$get" "$item::obj" fails
        WVPASS given "$existing_id" "$get" "$item::obj:=" fails
        WVPASS given "$existing_id" "$get" "$item::obj:f" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_+" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_=" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_f" fails

        if [[ "$existing_type" =~ tag ]]; then
            WVPASS given "$existing_id" "$get" "$item::obj:+" \
                produces new-save obj/latest '' "$item_id" "$item_id" \
                matching "$src_dir" \
                only-heads obj only-tags obj
            WVPASS given "$existing_id" "$get" "$item::.tag/obj" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:+" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:=" \
                produces "$type" "$item_id" matching "$item_id" \
                only-heads '' only-tags obj
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:f" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_+" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_=" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_f" fails
        else  # Make sure an existing head doesn't have an effect.
            WVPASS given "$existing_id" "$get" "$item::obj:+" \
                produces new-save obj/latest '' "$item_id" "$item_id" \
                matching "$src_dir" \
                only-heads obj only-tags ''
            WVPASS given "$existing_id" "$get" "$item::.tag/obj" \
                produces "$type" "$item_id" matching "$item_id" \
                only-heads obj only-tags obj
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:+" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:=" \
                produces "$type" "$item_id" matching "$item_id" \
                only-heads obj only-tags obj
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:f" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_+" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_=" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj:_f" fails
        fi
    done

    test-item-to-root "$get" "$type" "$item"
}


test-committish-src()
{
    local get="$1"
    local type="$2"
    local item="$3"
    local save_path="$4"
    local ref_name="$5"
    local commit_id="$6"
    local tree_id="$7"
    local src_subpath="$8"
    local src_dir="$9"
    local expected_heads="${10}"
    local expected_tags="${11}"

    local tagged_src
    #local ref_name
    if test "${item:0:5}" = .tag/; then
        #ref_name="${item:5}"
        tagged_src=true
        source_desc=tag
    else
        #ref_name="$item"
        source_desc=path
    fi

    WVSTART "given nothing, $get $type $source_desc"
    WVPASS given nothing "$get" "$item:::_+" fails
    WVPASS given nothing "$get" "$item:::_=" fails
    WVPASS given nothing "$get" "$item:::_f" fails
    WVPASS given nothing "$get" "$item::branch:_" fails
    WVPASS given nothing "$get" "$item::branch:_+" fails
    WVPASS given nothing "$get" "$item::branch:_=" fails
    WVPASS given nothing "$get" "$item::branch:_f" fails
    WVPASS given nothing "$get" "$item::.tag/x:+" fails
    WVPASS given nothing "$get" "$item::.tag/x:f" fails
    WVPASS given nothing "$get" "$item::.tag/x:_" fails
    WVPASS given nothing "$get" "$item::.tag/x:_+" fails
    WVPASS given nothing "$get" "$item::.tag/x:_=" fails
    WVPASS given nothing "$get" "$item::.tag/x:_f" fails

    # Wait, why do I need both $ref_name and $expected_*?
    # FIXME: was expected_* worth it?  Did it save anything?

    for spec in "$item" "$item::" "$item:::="; do
        if test "$tagged_src"; then
            WVPASS given nothing "$get" "$spec" \
                produces tagged-save "$ref_name" "$src_subpath" "$commit_id" "$tree_id" \
                matching "$src_dir" \
                only-heads "$expected_heads" \
                only-tags "$expected_tags"
        else  # Not a tag source.
            WVPASS given nothing "$get" "$spec" \
                produces save "$ref_name/latest" "$src_subpath" "$commit_id" "$tree_id" \
                matching "$src_dir" \
                only-heads "$expected_heads" \
                only-tags "$expected_tags"
        fi
    done

    if test "$tagged_src"; then
        WVPASS given nothing "$get" "$item:::f" fails
    else  # Not a tag source.
        WVPASS given nothing "$get" "$item:::f" \
            produces save "$ref_name/latest" "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads "$expected_heads" \
            only-tags "$expected_tags"
    fi

    WVPASS given nothing "$get" "$item::branch:+" \
        produces new-save branch/latest "$src_subpath" "$commit_id" "$tree_id" \
        matching "$src_dir" \
        only-heads branch only-tags ''

    for spec in "$item:branch" "$item::branch" "$item::branch:=" "$item::branch:f"; do
        WVPASS given nothing "$get" "$spec" \
            produces save branch/latest "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads branch only-tags ''
    done

    WVPASS given nothing "$get" "$item:::_" \
        produces commit "$commit_id" matching "$commit_id" \
        only-heads '' only-tags ''

    for spec in "$item:.tag/x" "$item::.tag/x" "$item::.tag/x:="; do
        WVPASS given nothing "$get" "$spec" \
            produces tagged-save x "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads '' \
            only-tags x
    done

    # We have to use commit-1, so that it'll be an ancestor of all the
    # heads we're going to use for $item.
    declare -a existing_types=("blob tag" "tree tag" "commit tag" \
        "ancestor commit head" "matching commit head" "unrelated commit head")
    declare -a existing_ids=( \
        .tag/tinyfile::.tag/obj .tag/tree-1::.tag/obj .tag/commit-1::.tag/obj \
        .tag/commit-1::obj .tag/commit-2::obj unrelated-branch/latest::obj)
    for ((i=0; i < ${#existing_types[@]}; i++)); do
        local existing_type="${existing_types[$i]}"
        local existing_id="${existing_ids[$i]}"

        if [[ "$existing_type" =~ tag ]]; then
            local ex_tags=obj
            local ex_heads=''
        else
            local ex_tags=''
            local ex_heads=obj
        fi

        WVSTART "given $existing_type, $get $type $source_desc"
        # Always fails (whether tag or branch dest).
        WVPASS given "$existing_id" "$get" "$item::obj:_" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_+" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_=" fails
        WVPASS given "$existing_id" "$get" "$item::obj:_f" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:+" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:f" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_+" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_=" fails
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:_f" fails

        # Always OK.
        WVPASS given "$existing_id" "$get" "$item::.tag/obj:=" \
            produces tagged-save obj "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads "$ex_heads" \
            only-tags "$ex_tags obj"

        WVPASS given "$existing_id" "$get" "$item::obj:=" \
            produces save obj/latest "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads "$ex_heads obj" \
            only-tags "$ex_tags"

        WVPASS given "$existing_id" "$get" "$item::obj:+" \
            produces new-save obj/latest "$src_subpath" "$commit_id" "$tree_id" \
            matching "$src_dir" \
            only-heads "$ex_heads obj" \
            only-tags "$ex_tags"

        if [[ "$existing_type" =~ tag ]]; then
            WVPASS given "$existing_id" "$get" "$item:.tag/obj" fails
            WVPASS given "$existing_id" "$get" "$item::.tag/obj" fails
        else  # Existing obj is a branch.
            for spec in "$item:.tag/obj" "$item::.tag/obj"; do
                WVPASS given "$existing_id" "$get" "$spec" \
                    produces tagged-save obj "$src_subpath" "$commit_id" "$tree_id" \
                    matching "$src_dir" \
                    only-heads "$ex_heads" \
                    only-tags "$ex_tags obj"

            done
        fi

        if [[ "$existing_type" =~ tag ]] \
            || ! [[ "$existing_type" =~ unrelated ]] ; then
            for spec in "$item:obj" "$item::obj" "$item::obj:f"; do
                WVPASS given "$existing_id" "$get" "$spec" \
                    produces save obj/latest "$src_subpath" "$commit_id" "$tree_id" \
                    matching "$src_dir" \
                    only-heads "$ex_heads obj" \
                    only-tags "$ex_tags"
            done
        else
            WVPASS given "$existing_id" "$get" "$item:obj" fails
            WVPASS given "$existing_id" "$get" "$item::obj" fails
            WVPASS given "$existing_id" "$get" "$item::obj:f" fails
        fi
    done

    test-item-to-root "$get" "$type" "$item"
}


# Setup.
WVPASS cd "$tmpdir"

WVPASS mkdir src src/x src/x/y
WVPASS bup random 1k > src/1
WVPASS bup random 1k > src/x/2

export BUP_DIR=get-src
WVPASS bup init
WVPASS bup index src
WVPASS bup save -tcn unrelated-branch src | tee save-output.log
WVPASS bup save -tcn src src | tee save-output.log
src_tree1_id=$(WVPASS head -n 1 save-output.log) || exit $?
src_commit1_id=$(WVPASS tail -n -1 save-output.log) || exit $?
src_save1=$(WVPASS bup ls src | WVPASS head -n 1) || exit $?
WVPASS cp -a src src-1

# Make a copy the current state of src so we'll have an ancestor.
cp -a get-src/refs/heads/src get-src/refs/heads/src-ancestor

WVPASS echo -n 'xyzzy' > src/tiny-file
WVPASS bup index src
WVPASS bup tick # Make sure the save names are different.
WVPASS bup save -tcn src src | tee save-output.log
src_tree2_id=$(WVPASS head -n 1 save-output.log) || exit $?
src_commit2_id=$(WVPASS tail -n -1 save-output.log) || exit $?
src_save2=$(WVPASS bup ls src | WVPASS head -n 2 | WVPASS tail -n 1) || exit $?
WVPASS mv src src-2

src_root="$(pwd)/src"

subtree_path=src-2/x
subtree_vfs_path="$src_root/x"
# No support for "ls -d", so grep...
subtree_id=$(WVPASS bup ls -s "src/latest$src_root" | WVPASS grep x \
    | WVPASS cut -d' ' -f 1) || exit $?

# With a tiny file, we'll get a single blob, not a chunked tree.
tinyfile_path="$src_root/tiny-file"
tinyfile_id=$(WVPASS bup ls -s "src/latest$tinyfile_path" \
    | WVPASS cut -d' ' -f 1) || exit $?

bup tag tinyfile "$tinyfile_id"
bup tag subtree "$subtree_id"
bup tag tree-1 "$src_tree1_id"
bup tag tree-2 "$src_tree2_id"
bup tag commit-1 "$src_commit1_id"
bup tag commit-2 "$src_commit2_id"
git --git-dir="$BUP_DIR"  branch commit-1 "$src_commit1_id"
git --git-dir="$BUP_DIR"  branch commit-2 "$src_commit2_id"


# Run tests.

if test "$BUP_TEST_LEVEL" = 11; then
    methods="get get-on get-to"
else
    methods="get get-on"
fi

for method in $methods; do

    if test "$BUP_TEST_LEVEL" = 11; then
        test-blob-src "$method" .tag/tinyfile "$tinyfile_id"
        test-blob-src "$method" "src/latest$tinyfile_path" "$tinyfile_id"

        test-tree-src "$method" .tag/subtree "$subtree_id" "$subtree_path"
    fi

    test-tree-src "$method" "src/latest$subtree_vfs_path" "$subtree_id" "$subtree_path"

    test-committish-src "$method" \
        commit .tag/commit-2 .tag/commit-2 commit-2 \
        "$src_commit2_id" "$src_tree2_id" \
        "$(pwd)/src" src-2 \
        '' commit-2

    test-committish-src "$method" \
        save src/latest src/latest src \
        "$src_commit2_id" "$src_tree2_id" \
        "$(pwd)/src" src-2 \
        src ''

    test-committish-src "$method" \
        branch src src/latest src \
        "$src_commit2_id" "$src_tree2_id" \
        "$(pwd)/src" src-2 \
        src ''
done

WVSTART "checked $given_count cases"

WVPASS rm -rf "$tmpdir"
