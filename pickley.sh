#!/bin/bash

url=https://github.com/zsimic/pickley/releases

target=$1
if [[ -z $target ]]; then
    target=~/.local/bin
    if [[ ! -d $target ]]; then
        target=/usr/local/bin
    fi
elif [[ ! -d $target ]]; then
    echo "Folder $target does not exist"
    exit 1
fi

version=`curl -s $url/latest | egrep -o 'tag/[^"]+' | cut -d/ -f2`

echo curl -sLo $target/pickley $url/download/$version/pickley
