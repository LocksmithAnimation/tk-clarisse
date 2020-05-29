import sys
import sgtk


if sys.platform == "win32":
    win_32_api = sgtk.platform.import_framework(
        "ls-framework-shotgunutils",
        "win_32_utils.win_32_api"
    )