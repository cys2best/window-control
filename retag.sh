#!/bin/bash
VERSION=$1; git tag -d "v$VERSION" && git tag "v$VERSION" && git push --force origin "v$VERSION"
