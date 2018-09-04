#!/bin/bash

target=$1
if [[ -z $target ]]; then
    target=/usr/local/bin
fi
if [[ ! -d $target ]]; then
    echo "Folder $target does not exist"
    exit 1
fi

url=`curl -s https://pypi.org/pypi/pickley/json | grep -Eo '"download_url":"([^"]+)"' | cut -d'"' -f4`
echo curl -sLo $target/pickley $url
