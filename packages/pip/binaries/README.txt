# Platform-specific pharos CLI binaries go here.
# These are built from the pharos-cli Go project and shipped as package data.
#
# Expected files:
#   pharos-linux-amd64      (Linux x86_64)
#   pharos-darwin-amd64     (macOS Intel)
#   pharos-darwin-arm64     (macOS Apple Silicon)
#   pharos-windows-amd64.exe (Windows x86_64)
#
# The CI/CD pipeline cross-compiles these and places them here before
# building the wheel. In development, you can symlink the local build:
#   ln -s ../../../../../pharos-cli/pharos-cli binaries/pharos-linux-amd64
