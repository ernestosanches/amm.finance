#!/usr/bin/env bash
# Reproducible headless rendering of the Plotly HTML figures to PNG, using OPEN-SOURCE
# Chromium (the Chromium-project build bundled by Playwright — no Google account, no
# proprietary Chrome). Lets out/*.html be screenshotted and verified without a desktop browser.
#
# Two separate steps so the privileged part is isolated:
#
#   1) ADMIN (once)  — Chromium's system libraries via apt. Runs as root directly, or via sudo.
#        bash tools.sh --install-admin          # as root  (no sudo needed/used)
#        sudo bash tools.sh --install-admin     # as a sudo user
#
#   2) USER          — playwright package + open-source Chromium + a self-test render. No sudo.
#        bash tools.sh --install
#
#   render:          python3 render_figs.py   (or:  bash tools.sh --render [args])
#
# Idempotent — safe to re-run either step.
set -euo pipefail
cd "$(dirname "$0")"

# Chromium runtime libraries for Ubuntu 22.04, as computed by
# `python3 -m playwright install-deps --dry-run chromium`. Embedded so --install-admin needs
# ONLY apt — no Python/Playwright in root's environment.
CHROMIUM_APT_DEPS="fonts-ipafont-gothic fonts-liberation fonts-noto-color-emoji \
fonts-tlwg-loma-otf fonts-unifont fonts-wqy-zenhei libasound2 libasound2-data \
libatk-bridge2.0-0 libatk1.0-0 libatk1.0-data libatspi2.0-0 libavahi-client3 \
libavahi-common-data libavahi-common3 libcairo-gobject2 libcairo2 libcups2 libfontenc1 \
libfreetype6 libglib2.0-0 libglib2.0-bin libice6 libnspr4 libnss3 libsm6 libxaw7 \
libxcomposite1 libxdamage1 libxfont2 libxkbfile1 libxmu6 libxmuu1 libxpm4 libxt6 \
x11-xkb-utils xauth xfonts-cyrillic xfonts-encodings xfonts-scalable xfonts-utils \
xserver-common xvfb"

install_admin() {
  local APT
  if [ "$(id -u)" -eq 0 ]; then
    APT="apt-get"                      # already root: apt directly, no sudo
  elif command -v sudo >/dev/null 2>&1; then
    echo "Not root — using sudo for apt."
    APT="sudo apt-get"
  else
    echo "ERROR: need root. Run 'bash tools.sh --install-admin' as root, or install sudo." >&2
    exit 1
  fi
  $APT update -qq
  $APT install -y -qq $CHROMIUM_APT_DEPS
  echo "admin install OK — Chromium system libraries present."
}

install_user() {
  python3 -m pip install --user --quiet playwright
  python3 -m playwright install chromium     # open-source Chromium -> ~/.cache/ms-playwright
  python3 render_figs.py --self-test
  echo "user install OK — render with: python3 render_figs.py"
}

case "${1:-}" in
  --install-admin) install_admin ;;
  --install)       install_user ;;
  --render)        python3 render_figs.py "${@:2}" ;;
  *)
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'   # print the header usage block
    ;;
esac
