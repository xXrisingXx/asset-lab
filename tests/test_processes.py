import ctypes
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import lab_core


@unittest.skipUnless(os.name == 'nt', 'Windows process guard')
class ProcessGuardTests(unittest.TestCase):
    def test_native_snapshot_sees_this_process_without_spawning_tasklist(self):
        with patch.object(lab_core.subprocess, 'run', side_effect=AssertionError('No subprocess needed')):
            self.assertTrue(any(pid == os.getpid() for name, pid in lab_core.windows_processes()))

    def test_name_filter_and_unscoped_guard(self):
        with patch.object(lab_core, 'windows_processes', return_value=[('other.exe', 1), ('MITOSIS.EXE', 2)]):
            self.assertEqual(lab_core.game_processes(), ['MITOSIS.EXE'])

    def test_same_name_from_another_install_does_not_block_this_install(self):
        # The native image-path query identifies this Python process despite the mocked snapshot name.
        with tempfile.TemporaryDirectory() as tmp, patch.object(lab_core, 'windows_processes', return_value=[('Mitosis.exe', os.getpid())]):
            self.assertEqual(lab_core.game_processes(Path(tmp)), [])

    def test_unreadable_identity_keeps_the_game_guard_closed(self):
        for handle in (0, 123):
            kernel = Mock()
            kernel.OpenProcess.return_value = handle
            kernel.QueryFullProcessImageNameW.return_value = False
            with patch.object(lab_core, 'windows_processes', return_value=[('Mitosis.exe', 42)]), patch.object(ctypes, 'WinDLL', return_value=kernel):
                self.assertEqual(lab_core.game_processes(Path.cwd()), ['Mitosis.exe'])
            if handle: kernel.CloseHandle.assert_called_once_with(handle)

    def test_snapshot_failure_never_reports_game_closed(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
        with patch.object(ctypes, 'WinDLL', return_value=kernel), self.assertRaises(ValueError):
            lab_core.game_processes()
        kernel.CloseHandle.assert_not_called()

    def test_incomplete_snapshot_fails_closed_and_releases_handle(self):
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = 123
        kernel.Process32FirstW.return_value = False
        with patch.object(ctypes, 'WinDLL', return_value=kernel), patch.object(ctypes, 'get_last_error', return_value=5), self.assertRaises(ValueError):
            lab_core.game_processes()
        kernel.CloseHandle.assert_called_once_with(123)
