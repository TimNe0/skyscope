#!/usr/bin/env bash
# Run SkyScope in the EMF badge simulator. Needs the sim checked out next door:
#   git clone https://github.com/emfcamp/badge-2024-software ../badge-2024-software
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM="${SIM_DIR:-$APP/../badge-2024-software/sim}"

if [ ! -f "$SIM/run.py" ]; then
    echo "simulator not found at $SIM (clone badge-2024-software next door, or set SIM_DIR)" >&2
    exit 1
fi

# The sim's override launcher trips a circular import; pre-import the scheduler.
grep -q "skyscope-sim-fix" "$SIM/run.py" || \
    sed -i '/^def replace_launcher/a\    import system.scheduler  # skyscope-sim-fix' "$SIM/run.py"

ln -sfn "$APP" "$SIM/apps/skyscope"
exec python3 "$SIM/run.py" skyscope.FlightRadarApp
