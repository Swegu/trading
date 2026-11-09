#!/bin/sh
set -eu
mkdir -p /data
chown -R markets:markets /data
exec gosu markets "$@"
