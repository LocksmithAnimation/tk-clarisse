import sys
import sgtk


if sys.platform == "win32":
    win_32_api = sgtk.platform.import_framework(
        "tk-framework-adobe",
        "tk_framework_adobe_utils.win_32_api"
    )