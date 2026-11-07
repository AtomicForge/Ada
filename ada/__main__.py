# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

os.environ["GDK_BACKEND"] = "wayland"  # Wayland only, no X11 fallback
os.environ["LANGUAGE"] = "en_US:en"  # the app is English only

from ada.app import main

sys.exit(main())
