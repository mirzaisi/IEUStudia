"""Start with Windows: IEUStudia reads Windows' own switch for it (Task Manager's Startup apps), turns that on again
when it is asked to in IEUStudia, and a registry write Windows or an antivirus refuses is said, never an error that
stops the setup's Background step ('[WinError 5]', and Continue did nothing)."""

import sys

import pytest

from studia import appconfig, launch

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows Run key")
BASE = r"Software\IEUStudiaTests"


def _delete_tree(winreg, path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            subs = []
            i = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
        for sub in subs:
            _delete_tree(winreg, path + "\\" + sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


@pytest.fixture
def keys(monkeypatch):
    """The Run key and Windows' switches in a throwaway key (never the real ones)."""
    import winreg

    monkeypatch.setattr(launch, "RUN_KEY", BASE + r"\Run")
    monkeypatch.setattr(launch, "APPROVED_KEY", BASE + r"\StartupApproved\Run")
    yield winreg
    _delete_tree(winreg, BASE)


def _switch(winreg, first_byte):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, launch.APPROVED_KEY) as k:
        winreg.SetValueEx(k, launch.RUN_VALUE, 0, winreg.REG_BINARY, bytes([first_byte]) + bytes(11))


def test_windows_own_startup_apps_switch_is_read_and_turned_on_again_when_asked(keys):
    winreg = keys
    assert launch.set_startup(True) is True and launch.startup_enabled()
    _switch(winreg, 3)                                   # turned off in Task Manager → Startup apps
    assert not launch.startup_enabled()                  # Windows skips it: not shown as on (the tray's tick, Settings)
    assert launch.set_startup(True) is True and launch.startup_enabled()   # asked for in IEUStudia: on in Windows too
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, launch.APPROVED_KEY) as k:
        with pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(k, launch.RUN_VALUE)
    _switch(winreg, 2)                                   # on in Task Manager
    assert launch.startup_enabled()
    assert launch.set_startup(False) is True and not launch.startup_enabled()


def test_a_refused_registry_write_is_said_and_never_stops_the_setup(keys, monkeypatch):
    from studia import agent
    from studia.api import Api
    from studia.services import Services

    winreg = keys

    def refused(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(winreg, "CreateKey", refused)
    assert launch.set_startup(True) is False             # no exception: logged, and False
    monkeypatch.setattr(agent, "ensure_running", lambda: True)
    monkeypatch.setattr(agent, "running", lambda: True)
    api = Api(Services())
    monkeypatch.setattr(api, "_system_info", lambda: None)
    r = api.app_config({"tray": True, "start_with_windows": True, "notify_materials": True})
    assert r["ok"] and r["startup"] is False                       # the page says what to do and goes on
    assert r["app"]["notify_materials"] and appconfig.load().notify_materials     # the rest is saved
