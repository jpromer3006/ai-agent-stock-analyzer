#!/bin/zsh
#
# Launch Demo (Public Link).command — OROS with the public tunnel ON.
#
# Double-click THIS file on demo days when you want a shareable public
# URL for the audience. The regular Launch.command stays local-only
# (2026-08-06 security review: the tunnel has no login, so it is opt-in).
#
# When you're done demoing, close the Terminal window to kill the tunnel.

export OROS_TUNNEL=1
exec /bin/bash "$(cd "$(dirname "$0")" && pwd)/Launch.command"
