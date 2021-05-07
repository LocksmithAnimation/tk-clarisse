# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

"""A Clarisse engine for Tank."""

import os
import sys
import time
import re
import inspect
import logging
import traceback
from functools import wraps, partial

import tank
from tank.log import LogManager
from tank.platform import Engine
from tank.platform.constants import SHOTGUN_ENGINE_NAME
import ix

from clarisse_core import pyside_clarisse


__author__ = "Diego Garcia Huerta"
__contact__ = "https://www.linkedin.com/in/diegogh/"

# initialize our shotgun structure for the session
if not hasattr(ix, "shotgun"):
    # use a dummy class to keep references to menus
    ix.shotgun = lambda: None
    ix.shotgun.menu_callbacks = {}


def show_error(msg):
    print("Shotgun Error | Clarisse engine | %s " % msg)
    ix.application.message_box(
        msg,
        "Shotgun Error | Clarisse engine",
        ix.api.AppDialog.cancel(),
        ix.api.AppDialog.STYLE_OK,
    )


def show_warning(msg):
    ix.application.message_box(
        msg,
        "Shotgun Warning | Clarisse engine",
        ix.api.AppDialog.cancel(),
        ix.api.AppDialog.STYLE_OK,
    )


def show_info(msg):
    ix.application.message_box(
        msg,
        "Shotgun Info | Clarisse engine",
        ix.api.AppDialog.cancel(),
        ix.api.AppDialog.STYLE_OK,
    )


def display_error(msg):
    t = time.asctime(time.localtime())
    print("%s - Shotgun Error | Clarisse engine | %s " % (t, msg))
    ix.application.log_error("%s - Shotgun Error | Clarisse engine | %s " % (t, msg))


def display_warning(msg):
    t = time.asctime(time.localtime())
    ix.application.log_warning(
        "%s - Shotgun Warning | Clarisse engine | %s " % (t, msg)
    )


def display_info(msg):
    t = time.asctime(time.localtime())
    ix.application.log_info("%s - Shotgun Info | Clarisse engine | %s " % (t, msg))


def display_debug(msg):
    if os.environ.get("TK_DEBUG") == "1":
        t = time.asctime(time.localtime())
        ix.application.log_info("%s - Shotgun Debug | Clarisse engine | %s " % (t, msg))


# we use a trick with decorators to get some sort of event notification
# when the scene is saved/loaded, etc... we could use a timer similar to
# what tk-houdini uses but this other aproach is more generic
def wrapped(function, watcher, post_callback=None, pre_callback=None):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            if pre_callback is not None:
                pre_callback(watcher)

            result = function(*args, **kwargs)

            if post_callback is not None:
                post_callback(watcher)

            return result
        except:
            raise

    wrapper._original = function
    return wrapper


SCENE_EVENT_NAMES = (
    "new_project",
    "clear_project",
    "import_project",
    "load_project",
    "save_project",
    "load_startup_scene",
)

SCENE_QUIT_EVENT_NAME = "quit"


class SceneEventWatcher(object):
    """
    Encapsulates event handling for multiple scene events and routes them
    into a single callback.

    This uses monkey patching some of the functions in the clarisse application

    Specifying run_once=True in the constructor causes all events to be
    cleaned up after the first one has triggered
    """

    def __init__(self, cb_fn, run_once=False):
        """
        Constructor.

        :param cb_fn: Callback to invoke everytime a scene event happens.
        :param scene_events: List of scene events to watch for. Defaults to
            new, open and save.
        :param run_once: If True, the watcher will notify only on the first
            event. Defaults to False.
        """
        self.__cb_fn = cb_fn
        self.__run_once = run_once
        self.__wrapped_fns = {}

        # register scene event callbacks:
        self.start_watching()

    def start_watching(self):
        """
        Starts watching for scene events.
        """
        # if currently watching then stop:
        self.stop_watching()

        # now add callbacks to watch for some scene events:
        for event_name in SCENE_EVENT_NAMES:
            try:
                event_fn = getattr(ix.application, event_name)
                event_fn = wrapped(
                    event_fn,
                    self,
                    post_callback=SceneEventWatcher.__scene_event_callback,
                )
                self.__wrapped_fns[event_name] = event_fn
                setattr(ix.application, event_name, event_fn)
                display_debug("Registered callback on %s " % event_name)
            except Exception:
                traceback.print_exc()
                # report warning...
                continue

        # create a callback that will be run when Clarisse
        # exits so we can do some clean-up:
        event_fn = getattr(ix.application, SCENE_QUIT_EVENT_NAME)
        event_fn = wrapped(
            event_fn,
            self,
            pre_callback=SceneEventWatcher.__clarisse_exiting_callback,
        )
        self.__wrapped_fns[SCENE_QUIT_EVENT_NAME] = event_fn
        setattr(ix.application, SCENE_QUIT_EVENT_NAME, event_fn)

    def stop_watching(self):
        """
        Stops watching the Clarisse scene.
        """
        for event_name, event_fn in self.__wrapped_fns.items():
            setattr(ix.application, event_name, event_fn._original)
        self.__wrapped_fns = {}

    @staticmethod
    def __scene_event_callback(watcher):
        """
        Called on a scene event:
        """
        if watcher.__run_once:
            watcher.stop_watching()
        watcher.__cb_fn()

    @staticmethod
    def __clarisse_exiting_callback(watcher):
        """
        Called on Clarisse exit - should clean up any existing calbacks
        """
        watcher.stop_watching()


###############################################################################
# methods to support the state when the engine cannot start up
# for example if a non-tank file is loaded in clarisse


def refresh_engine(engine_name, prev_context, menu_name):
    """
    refresh the current engine
    """
    current_engine = tank.platform.current_engine()

    if not current_engine:
        # If we don't have an engine for some reason then we don't have
        # anything to do.
        return

    scene_name = ix.application.get_current_project_filename()

    # This is a File->New call, so we just leave the engine in the current
    # context and move on.
    if scene_name == "":
        if prev_context != current_engine.context:
            current_engine.change_context(prev_context)
        return

    # determine the tk instance and ctx to use:
    tk = current_engine.sgtk

    # loading a scene file
    new_path = os.path.abspath(scene_name)

    # this file could be in another project altogether, so create a new
    # API instance.
    try:
        tk = tank.tank_from_path(new_path)
        # and construct the new context for this path:
        ctx = tk.context_from_path(new_path, prev_context)
    except tank.TankError:
        try:
            ctx = current_engine.sgtk.context_from_entity_dictionary(
                current_engine.context.project
            )
        except tank.TankError:
            (exc_type, exc_value, exc_traceback) = sys.exc_info()
            message = ""
            message += "Shotgun Clarisse Engine cannot be started:.\n"
            message += "Please contact you technical support team for more "
            message += "information.\n\n"
            message += "Exception: %s - %s\n" % (exc_type, exc_value)
            message += "Traceback (most recent call last):\n"
            message += "\n".join(traceback.format_tb(exc_traceback))

            # build disabled menu
            create_sgtk_disabled_menu(menu_name)

            display_error(message)
            return

    # now remove the shotgun disabled menu if it exists.
    remove_sgtk_disabled_menu(menu_name)

    # shotgun menu may have been removed, so add it back in if its not already
    # there.
    current_engine.create_shotgun_menu()

    if ctx != tank.platform.current_engine().context:
        current_engine.change_context(ctx)


def on_scene_event_callback(engine_name, prev_context, menu_name):
    """
    Callback that's run whenever a scene is saved or opened.
    """
    try:
        refresh_engine(engine_name, prev_context, menu_name)
    except Exception:
        (exc_type, exc_value, exc_traceback) = sys.exc_info()
        message = ""
        message += (
            "Message: Shotgun encountered a problem changing the " "Engine's context.\n"
        )
        message += "Please contact you technical support team for more "
        message += "information.\n\n"
        message += "Exception: %s - %s\n" % (exc_type, exc_value)
        message += "Traceback (most recent call last):\n"
        message += "\n".join(traceback.format_tb(exc_traceback))
        show_error(message)


def sgtk_disabled_message():
    """
    Explain why tank is disabled.
    """
    msg = (
        "Shotgun integration is disabled because it cannot recognize "
        "the currently opened file.  Try opening another file or restarting "
        "Clarisse."
    )

    show_warning(msg)


def clear_sgtk_menu(menu_name):
    if not ix.is_gui_application():
        # don't create menu in not interactive mode
        return

    if get_sgtk_root_menu(menu_name):
        menu = ix.application.get_main_menu()
        menu.remove_all_commands(menu_name + ">")
        ix.shotgun.menu_callbacks = {}


def get_sgtk_root_menu(menu_name):
    menu = ix.application.get_main_menu()

    sg_menu = menu.get_item(menu_name + ">")
    if not sg_menu:
        sg_menu = menu.add_command(menu_name + ">")
    return sg_menu


def create_sgtk_disabled_menu(menu_name):
    """
    Render a special "shotgun is disabled" menu
    """
    if not ix.is_gui_application():
        # don't create menu in not interactive mode
        return

    sg_menu = get_sgtk_root_menu(menu_name)
    menu_item = menu_name + ">Sgtk is disabled."
    ix.shotgun.menu_callbacks[menu_item] = sgtk_disabled_message
    sg_menu.add_command_as_script(
        menu_name + ">Sgtk is disabled.",
        "ix.shotgun.menu_callbacks[%s]" % menu_item,
    )


def remove_sgtk_disabled_menu(menu_name):
    """
    Clear the shotgun menu
    """
    clear_sgtk_menu(menu_name)


###############################################################################
# The Tank Clarisse engine


class ClarisseEngine(Engine):
    """
    Toolkit engine for Clarisse.
    """

    _DIALOG_PARENT = None
    _WIN32_CLARISSE_MAIN_HWND = None
    _PROXY_WIN_HWND = None

    def __get_platform_resource_path(self, filename):
        """
        Returns the full path to the given platform resource file or folder.
        Resources reside in the core/platform/qt folder.
        :return: full path
        """
        tank_platform_folder = os.path.abspath(inspect.getfile(tank.platform))
        return os.path.join(tank_platform_folder, "qt", filename)

    def __toggle_debug_logging(self):
        """
        Toggles global debug logging on and off in the log manager.
        This will affect all logging across all of toolkit.
        """
        # flip debug logging
        LogManager().global_debug = not LogManager().global_debug

    def __open_log_folder(self):
        """
        Opens the file system folder where log files are being stored.
        """
        self.log_info("Log folder location: '%s'" % LogManager().log_folder)

        if self.has_ui:
            # only import QT if we have a UI
            from sgtk.platform.qt import QtGui, QtCore

            url = QtCore.QUrl.fromLocalFile(LogManager().log_folder)
            status = QtGui.QDesktopServices.openUrl(url)
            if not status:
                self._engine.log_error("Failed to open folder!")

    def __register_open_log_folder_command(self):
        """
        # add a 'open log folder' command to the engine's context menu
        # note: we make an exception for the shotgun engine which is a
        # special case.
        """
        if self.name != SHOTGUN_ENGINE_NAME:
            self.register_command(
                "Open Log Folder",
                self.__open_log_folder,
                {
                    "short_name": "open_log_folder",
                    "icon": self.__get_platform_resource_path("folder_256.png"),
                    "description": (
                        "Opens the folder where log files are being stored."
                    ),
                    "type": "context_menu",
                },
            )

    @property
    def context_change_allowed(self):
        """
        Whether the engine allows a context change without the need for a
        restart.
        """
        return True

    @property
    def host_info(self):
        """
        :returns: A dictionary with information about the application hosting
                  this engine.

        The returned dictionary is of the following form on success:

            {
                "name": "Clarisse",
                "version": "2017 Update 4",
            }

        The returned dictionary is of following form on an error preventing
        the version identification.

            {
                "name": "Clarisse",
                "version: "unknown"
            }
        """

        host_info = {"name": "Clarisse", "version": "unknown"}
        try:
            clarisse_ver = ix.application.get_version_name()
            host_info["version"] = clarisse_ver
        except:
            # Fallback to 'Clarisse' initialized above
            pass
        return host_info

    @property
    def tk_clarisse(self):
        if not self._tk_clarisse:
            self._tk_clarisse = self.import_module("tk_clarisse")
        return self._tk_clarisse

    ###########################################################################
    # init and destroy

    def pre_app_init(self):
        """
        Runs after the engine is set up but before any apps have been
        initialized.
        """
        # unicode characters returned by the shotgun api need to be converted
        # to display correctly in all of the app windows
        from tank.platform.qt import QtCore

        # tell QT to interpret C strings as utf-8
        utf8 = QtCore.QTextCodec.codecForName("utf-8")
        QtCore.QTextCodec.setCodecForCStrings(utf8)
        self.logger.debug("set utf-8 codec for widget text")
        win_32_utils = self.import_module("win_32_utils")
        self.win_32_api = win_32_utils.win_32_api
        self._tk_clarisse = None
        self.__qt_dialogs = []

    def init_engine(self):
        """
        Initializes the Clarisse engine.
        """
        self.logger.debug("%s: Initializing...", self)

        # check that we are running an ok version of clarisse
        current_os = sys.platform.lower()
        if current_os not in ["darwin", "win32", "linux64"]:
            raise tank.TankError(
                "The current platform is not supported! Supported platforms "
                "are Mac, Linux 64 and Windows 64."
            )

        clarisse_build_version = ix.application.get_version()
        clarisse_ver = float(".".join(clarisse_build_version.split(".")[:2]))

        if clarisse_ver < 3.6:
            msg = "Shotgun integration is not compatible with Clarisse "
            msg += "versions older than 3.6"
            raise tank.TankError(msg)

        if clarisse_ver > 5.0:
            # show a warning that this version of Clarisse isn't yet fully
            # tested with Shotgun:
            msg = (
                "The Shotgun Pipeline Toolkit has not yet been fully tested "
                "with Clarisse %s.\n"
                "You can continue to use Toolkit but you may experience bugs "
                "or instability."
                "\n\nUse at your own risk." % (clarisse_ver)
            )

            # determine if we should show the compatibility warning dialog:
            show_warning_dlg = (
                self.has_ui and "SGTK_COMPATIBILITY_DIALOG_SHOWN" not in os.environ
            )
            if show_warning_dlg:
                # make sure we only show it once per session:
                os.environ["SGTK_COMPATIBILITY_DIALOG_SHOWN"] = "1"

                # split off the major version number - accomodate complex
                # version strings and decimals:
                major_version_number_str = clarisse_build_version.split(".")[0]
                if major_version_number_str and major_version_number_str.isdigit():
                    # check against the compatibility_dialog_min_version:
                    if int(major_version_number_str) < self.get_setting(
                        "compatibility_dialog_min_version"
                    ):
                        show_warning_dlg = False

            if show_warning_dlg:
                # Note, title is padded to try to ensure dialog isn't insanely
                # narrow!
                show_info(msg)

            # always log the warning to the script editor:
            self.logger.warning(msg)

            # In the case of Clarisse on Windows, we have the possility of
            # locking up if we allow the PySide shim to import
            # QtWebEngineWidgets. We can stop that happening here by setting
            # the environment variable.

            if current_os.startswith("win"):
                self.logger.debug(
                    "Clarisse on Windows can deadlock if QtWebEngineWidgets "
                    "is imported. "
                    "Setting SHOTGUN_SKIP_QTWEBENGINEWIDGETS_IMPORT=1..."
                )
                os.environ["SHOTGUN_SKIP_QTWEBENGINEWIDGETS_IMPORT"] = "1"

        # default menu name is Shotgun but this can be overriden
        # in the configuration to be Sgtk in case of conflicts
        self._menu_name = "Shotgun"
        if self.get_setting("use_sgtk_as_menu_name", False):
            self._menu_name = "Sgtk"

        if self.get_setting("automatic_context_switch", True):
            # need to watch some scene events in case the engine needs
            # rebuilding:

            cb_fn = partial(
                on_scene_event_callback,
                engine_name=self.instance_name,
                prev_context=self.context,
                menu_name=self._menu_name,
            )

            self.__watcher = SceneEventWatcher(cb_fn, run_once=False)
            self.logger.debug("Registered open and save callbacks.")

    def create_shotgun_menu(self):
        """
        Creates the main shotgun menu in clarisse.
        Note that this only creates the menu, not the child actions
        :return: bool
        """

        # only create the shotgun menu if not in batch mode and menu doesn't
        # already exist
        if self.has_ui:

            self._menu_handle = get_sgtk_root_menu(self._menu_name)

            # create our menu handler
            self._menu_generator = self.tk_clarisse.MenuGenerator(
                self, self._menu_handle
            )
            self._menu_generator.create_menu()
            return True

        return False

    def _initialise_qapplication(self):
        """
        Ensure the QApplication is initialized
        """
        from sgtk.platform.qt import QtGui

        qt_app = QtGui.QApplication.instance()
        if qt_app is None:
            self.log_debug("Initialising main QApplication...")
            app_name = "Shotgun Toolkit for Substance Painter"
            qt_app = QtGui.QApplication([app_name])
            qt_app.setWindowIcon(QtGui.QIcon(self.icon_256))
            qt_app.setQuitOnLastWindowClosed(False)

            # Make the QApplication use the dark theme. Must be called after the QApplication is instantiated
            self._initialize_dark_look_and_feel()

        pyside_clarisse.exec_(qt_app)

    def post_app_init(self):
        """
        Called when all apps have initialized
        """
        if self.has_ui:
            self._initialise_qapplication()

        # for some readon this engine command get's lost so we add it back
        # self.__register_reload_command()
        self.create_shotgun_menu()

        # Run a series of app instance commands at startup.
        self._run_app_instance_commands()

    def post_context_change(self, old_context, new_context):
        """
        Runs after a context change. The Clarisse event watching will be
        stopped and new callbacks registered containing the new context
        information.

        :param old_context: The context being changed away from.
        :param new_context: The new context being changed to.
        """

        # restore the open log folder, it get's removed whenever the first time
        # a context is changed
        self.__register_open_log_folder_command()
        # self.__register_reload_command()

        if self.get_setting("automatic_context_switch", True):
            # We need to stop watching, and then replace with a new watcher
            # that has a callback registered with the new context baked in.
            # This will ensure that the context_from_path call that occurs
            # after a File->Open receives an up-to-date "previous" context.
            self.__watcher.stop_watching()

            cb_fn = partial(
                on_scene_event_callback,
                engine_name=self.instance_name,
                prev_context=self.context,
                menu_name=self._menu_name,
            )

            self.__watcher = SceneEventWatcher(cb_fn, run_once=False)
            self.logger.debug(
                "Registered new open and save callbacks before" " changing context."
            )

            # finally create the menu with the new context if needed
            if old_context != new_context:
                self.create_shotgun_menu()

    def _run_app_instance_commands(self):
        """
        Runs the series of app instance commands listed in the 'run_at_startup'
        setting of the environment configuration yaml file.
        """

        # Build a dictionary mapping app instance names to dictionaries of
        # commands they registered with the engine.
        app_instance_commands = {}
        for (command_name, value) in self.commands.items():
            app_instance = value["properties"].get("app")
            if app_instance:
                # Add entry 'command name: command function' to the command
                # dictionary of this app instance.
                command_dict = app_instance_commands.setdefault(
                    app_instance.instance_name, {}
                )
                command_dict[command_name] = value["callback"]

        # Run the series of app instance commands listed in the
        # 'run_at_startup' setting.
        for app_setting_dict in self.get_setting("run_at_startup", []):

            app_instance_name = app_setting_dict["app_instance"]
            # Menu name of the command to run or '' to run all commands of the
            # given app instance.
            setting_command_name = app_setting_dict["name"]

            # Retrieve the command dictionary of the given app instance.
            command_dict = app_instance_commands.get(app_instance_name)

            if command_dict is None:
                self.logger.warning(
                    (
                        "%s configuration setting 'run_at_startup'"
                        " requests app '%s' that is not installed."
                    ),
                    self.name,
                    app_instance_name,
                )
            else:
                if not setting_command_name:
                    # Run all commands of the given app instance.
                    # Run these commands once Clarisse will have completed its
                    # UI update and be idle in order to run them after the ones
                    # that restore the persisted Shotgun app panels.
                    for (
                        command_name,
                        command_function,
                    ) in command_dict.items():
                        self.logger.debug(
                            "%s startup running app '%s' command '%s'.",
                            self.name,
                            app_instance_name,
                            command_name,
                        )
                        clarisse.utils.executeDeferred(command_function)
                else:
                    # Run the command whose name is listed in the
                    # 'run_at_startup' setting.
                    # Run this command once Clarisse will have completed its
                    # UI update and be idle in order to run it after the ones
                    # that restore the persisted Shotgun app panels.
                    command_function = command_dict.get(setting_command_name)
                    if command_function:
                        self.logger.debug(
                            "%s startup running app '%s' command '%s'.",
                            self.name,
                            app_instance_name,
                            setting_command_name,
                        )
                        clarisse.utils.executeDeferred(command_function)
                    else:
                        known_commands = ", ".join(
                            "'%s'" % name for name in command_dict
                        )
                        self.logger.warning(
                            (
                                "%s configuration setting 'run_at_startup' "
                                "requests app '%s' unknown command '%s'. "
                                "Known commands: %s"
                            ),
                            self.name,
                            app_instance_name,
                            setting_command_name,
                            known_commands,
                        )

    def destroy_engine(self):
        """
        Stops watching scene events and tears down menu.
        """
        self.logger.debug("%s: Destroying...", self)
        clear_sgtk_menu(self._menu_name)

        for dialog in self.__qt_dialogs:
            dialog.hide()
            dialog.setParent(None)
            dialog.deleteLater()

        # Set our parent widget back to being owned by the window manager
        # instead of Clarisse's application window.
        if self._PROXY_WIN_HWND and sys.platform == "win32":
            self.win_32_api.SetParent(self._PROXY_WIN_HWND, 0)
            self._DIALOG_PARENT.deleteLater()
            self._DIALOG_PARENT = None

        if self.get_setting("automatic_context_switch", True):
            # stop watching scene events
            self.__watcher.stop_watching()

    def _win32_get_clarisse_main_hwnd(self):
        """
        Windows specific method to find the main Clarisse window
        handle (HWND)
        """
        if not self._WIN32_CLARISSE_MAIN_HWND:
            found_hwnds = self.win_32_api.find_windows(
                process_id=os.getpid(), class_name="FLTK", stop_if_found=False
            )
            if found_hwnds:
                self._WIN32_CLARISSE_MAIN_HWND = found_hwnds[-1]
        return self._WIN32_CLARISSE_MAIN_HWND

    def _win32_get_proxy_window(self):
        """
        Windows-specific method to get the proxy window that will 'own' all
        Toolkit dialogs.  This will be parented to the main Clarisse
        application.

        :returns: A QWidget that has been parented to Clarisse's window.
        """
        # Get the main Clarisse window:
        sp_hwnd = self._win32_get_clarisse_main_hwnd()
        win32_proxy_win = None
        proxy_win_hwnd = None

        if sp_hwnd:
            from sgtk.platform.qt import QtGui, QtCore

            # Create the proxy QWidget.
            win32_proxy_win = self.tk_clarisse.ProxyWidget(
                f=QtCore.Qt.FramelessWindowHint
            )
            window_title = "Shotgun Toolkit Parent Widget"
            win32_proxy_win.setWindowTitle(window_title)

            # We have to take different approaches depending on whether
            # we're using Qt4 (PySide) or Qt5 (PySide2). The functionality
            # needed to turn a Qt5 WId into an HWND is not exposed in PySide2,
            # so we can't do what we did below for Qt4.
            if QtCore.__version__.startswith("4."):
                proxy_win_hwnd = self.win_32_api.qwidget_winid_to_hwnd(
                    win32_proxy_win.winId(),
                )
            else:
                # With PySide2, we're required to look up our proxy parent
                # widget's HWND the hard way, following the same logic used
                # to find Clarisse's main window. To do that, we actually have
                # to show our widget so that Windows knows about it. We can make
                # it effectively invisible if we zero out its size, so we do that,
                # show the widget, and then look up its HWND by window title before
                # hiding it.
                win32_proxy_win.setGeometry(0, 0, 0, 0)
                win32_proxy_win.show()

                try:
                    proxy_win_hwnd_found = self.win_32_api.find_windows(
                        stop_if_found=False,
                        window_text="Shotgun Toolkit Parent Widget",
                        process_id=os.getpid(),
                    )
                finally:
                    win32_proxy_win.hide()

                if proxy_win_hwnd_found:
                    proxy_win_hwnd = proxy_win_hwnd_found[0]
        else:
            self.logger.debug(
                "Unable to determine the HWND of Clarisse itself. This means "
                "that we can't properly setup window parenting for Toolkit apps."
            )

        # Parent to the Clarisse application window if we found everything
        # we needed. If we didn't find our proxy window for some reason, we
        # will return None below. In that case, we'll just end up with no
        # window parenting, but apps will still launch.
        if proxy_win_hwnd is None:
            self.logger.warning(
                "Unable setup window parenting properly. Dialogs shown will "
                "not be parented to Clarisse, but they will still function "
                "properly otherwise."
            )
        else:
            # Set the window style/flags. We don't need or want our Python
            # dialogs to notify the Photoshop application window when they're
            # opened or closed, so we'll disable that behavior.
            win_ex_style = self.win_32_api.GetWindowLong(
                proxy_win_hwnd,
                self.win_32_api.GWL_EXSTYLE,
            )

            self.win_32_api.SetWindowLong(
                proxy_win_hwnd,
                self.win_32_api.GWL_EXSTYLE,
                win_ex_style | self.win_32_api.WS_EX_NOPARENTNOTIFY,
            )
            self.win_32_api.SetParent(proxy_win_hwnd, sp_hwnd)
            self._PROXY_WIN_HWND = proxy_win_hwnd

        return win32_proxy_win

    def set_proxy_size_to_hwnd(self):
        win_size = self.win_32_api.get_win_size(self._WIN32_CLARISSE_MAIN_HWND)
        self._DIALOG_PARENT.resize(
            win_size.w >= 0 and win_size.w or 0, win_size.h >= 0 and win_size.h or 0
        )
        self._DIALOG_PARENT.move(0, 0)

    def _get_dialog_parent(self):
        """
        We are using a proxy parent system similar to tk-photoshopcc.
        """
        if not self._DIALOG_PARENT:
            if sys.platform == "win32":
                # for windows, we create a proxy window parented to the
                # main application window that we can then set as the owner
                # for all Toolkit dialogs
                self._DIALOG_PARENT = self._win32_get_proxy_window()
            else:
                self._DIALOG_PARENT = QtGui.QApplication.activeWindow()

        if sys.platform == "win32":
            self.set_proxy_size_to_hwnd()

        return self._DIALOG_PARENT

    def show_dialog(self, title, bundle, widget_class, *args, **kwargs):
        if not self.has_ui:
            self.logger.error(
                "Sorry, this environment does not support UI display! Cannot "
                "show the requested window '%s'." % title
            )
            return None

        # create the dialog:
        dialog, widget = self._create_dialog_with_widget(
            title, bundle, widget_class, *args, **kwargs
        )

        self.__qt_dialogs.append(dialog)

        self.logger.debug("Showing dialog: {}".format(title))
        dialog.show()

        return widget

    @property
    def has_ui(self):
        """
        Detect and return if clarisse is running in batch mode
        """
        if not ix.is_gui_application():
            # batch mode or prompt mode
            return False
        else:
            return True

    ###########################################################################
    # logging

    def _emit_log_message(self, handler, record):
        """
        Called by the engine to log messages in Clarisse script editor.
        All log messages from the toolkit logging namespace will be passed to
        this method.

        :param handler: Log handler that this message was dispatched from.
                        Its default format is "[levelname basename] message".
        :type handler: :class:`~python.logging.LogHandler`
        :param record: Standard python logging record.
        :type record: :class:`~python.logging.LogRecord`
        """
        # Give a standard format to the message:
        #     Shotgun <basename>: <message>
        # where "basename" is the leaf part of the logging record name,
        # for example "tk-multi-shotgunpanel" or "qt_importer".
        if record.levelno < logging.INFO:
            formatter = logging.Formatter("Debug: Shotgun %(basename)s: %(message)s")
        else:
            formatter = logging.Formatter("Shotgun %(basename)s: %(message)s")

        msg = formatter.format(record)

        # Select Clarisse display function to use according to the logging
        # record level.
        if record.levelno >= logging.ERROR:
            fct = display_error
        elif record.levelno >= logging.WARNING:
            fct = display_warning
        elif record.levelno >= logging.INFO:
            fct = display_info
        else:
            fct = display_debug

        # Display the message in Clarisse script editor in a thread safe manner
        self.async_execute_in_main_thread(fct, msg)

    ###########################################################################
    # scene and project management

    def close_windows(self):
        """
        Closes the various windows (dialogs, panels, etc.) opened by the engine
        """

        # Make a copy of the list of Tank dialogs that have been created by the
        # engine and are still opened since the original list will be updated
        # when each dialog is closed.
        opened_dialog_list = self.created_qt_dialogs[:]

        # Loop through the list of opened Tank dialogs.
        for dialog in opened_dialog_list:
            dialog_window_title = dialog.windowTitle()
            try:
                # Close the dialog and let its close callback remove it from
                # the original dialog list.
                self.logger.debug("Closing dialog %s.", dialog_window_title)
                dialog.close()
            except Exception as exception:
                traceback.print_exc()
                self.logger.error(
                    "Cannot close dialog %s: %s", dialog_window_title, exception
                )
