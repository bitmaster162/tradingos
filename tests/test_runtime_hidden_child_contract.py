import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(r"C:\Users\coins\TradingOS\Active")
HELPER = ROOT / "ops" / "autostart" / "TradingOSChildProcess.ps1"
WATCHDOG_LOOP = ROOT / "ops" / "autostart" / "Run-CexFundingFreshnessWatchdogLoop.ps1"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")


class HiddenChildContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tradingos-hidden-child-")
        self.work = Path(self.temp_dir.name)
        self.child = self.work / "child process ünicode.py"
        self.stdout = self.work / "stdout.log"
        self.stderr = self.work / "stderr.log"
        self.child.write_text(
            textwrap.dedent(
                """
                import argparse
                import ctypes
                import json
                import os
                import subprocess
                import sys
                import time
                from pathlib import Path

                parser = argparse.ArgumentParser()
                parser.add_argument("--value", default="")
                parser.add_argument("--sleep", type=float, default=0)
                parser.add_argument("--exit-code", type=int, default=0)
                parser.add_argument("--stdout-bytes", type=int, default=0)
                parser.add_argument("--stderr-bytes", type=int, default=0)
                parser.add_argument("--spawn-grandchild-sleep", type=float, default=0)
                parser.add_argument("--grandchild-pid-file", default="")
                args = parser.parse_args()
                if args.spawn_grandchild_sleep:
                    grandchild_code = (
                        "import os,sys,time; from pathlib import Path; "
                        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='ascii'); "
                        "print('grandchild-stdout', flush=True); "
                        "print('grandchild-stderr', file=sys.stderr, flush=True); "
                        "time.sleep(float(sys.argv[2]))"
                    )
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            grandchild_code,
                            args.grandchild_pid_file,
                            str(args.spawn_grandchild_sleep),
                        ],
                        stdout=sys.stdout,
                        stderr=sys.stderr,
                        close_fds=False,
                    )
                if args.sleep:
                    time.sleep(args.sleep)
                console_window = int(ctypes.windll.kernel32.GetConsoleWindow()) if os.name == "nt" else -1
                print(json.dumps({
                    "value": args.value,
                    "pid": os.getpid(),
                    "python_utf8": os.environ.get("PYTHONUTF8"),
                    "python_io_encoding": os.environ.get("PYTHONIOENCODING"),
                    "console_window": console_window,
                }, ensure_ascii=False))
                if args.stdout_bytes:
                    sys.stdout.write("O" * args.stdout_bytes + "\\n")
                print("stderr-" + args.value, file=sys.stderr)
                if args.stderr_bytes:
                    sys.stderr.write("E" * args.stderr_bytes + "\\n")
                raise SystemExit(args.exit_code)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def run_powershell(self, name: str, source: str, *, timeout: int = 20):
        harness = self.work / name
        harness.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8-sig")
        return subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            cwd=self.work,
            text=True,
            encoding="utf-8-sig",
            capture_output=True,
            timeout=timeout,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def prepare_isolated_watchdog_runtime(
        self,
        *,
        health_exit: int = 0,
        incident_exit: int = 0,
        health_requests_shutdown: bool = False,
        incident_requests_shutdown: bool = False,
    ):
        root = self.work / f"runtime-{time.monotonic_ns()}"
        autostart = root / "ops" / "autostart"
        tools = root / "tools"
        configs = root / "configs"
        autostart.mkdir(parents=True)
        tools.mkdir(parents=True)
        configs.mkdir(parents=True)
        shutil.copy2(HELPER, autostart / HELPER.name)
        shutil.copy2(WATCHDOG_LOOP, autostart / WATCHDOG_LOOP.name)
        (autostart / "TradingOSRuntimeShutdownGate.ps1").write_text(
            textwrap.dedent(
                """
                function Test-TradingOSRuntimeShutdownRequested {
                    param([string]$Root, [string]$AllowedAttemptId)
                    return (Test-Path -LiteralPath (Join-Path $Root 'shutdown.flag') -PathType Leaf)
                }
                """
            ).strip()
            + "\n",
            encoding="utf-8-sig",
        )

        def write_tool(path: Path, marker: str, exit_code: int, request_shutdown: bool) -> None:
            path.write_text(
                textwrap.dedent(
                    f"""
                    import pathlib
                    import sys
                    root = pathlib.Path(__file__).resolve().parent.parent
                    (root / {marker!r}).write_text("ran", encoding="utf-8")
                    if {request_shutdown!r}:
                        (root / "shutdown.flag").write_text("stop", encoding="utf-8")
                    raise SystemExit({exit_code})
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

        write_tool(
            tools / "cex_funding_freshness_watchdog.py",
            "health.marker",
            health_exit,
            health_requests_shutdown,
        )
        write_tool(
            tools / "cex_funding_freshness_incident_alert.py",
            "incident.marker",
            incident_exit,
            incident_requests_shutdown,
        )
        return root, autostart / WATCHDOG_LOOP.name

    def run_isolated_watchdog(self, loop: Path, *, timeout: int = 30):
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(loop),
                "-SleepSeconds",
                "60",
                "-ChildTimeoutSeconds",
                "15",
                "-PythonPath",
                sys.executable,
            ],
            cwd=loop.parents[2],
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    @classmethod
    def _assert_pid_stops(cls, pid: int, timeout: float = 4) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and cls._pid_exists(pid):
            time.sleep(0.1)
        if cls._pid_exists(pid):
            raise AssertionError(f"PID {pid} is still active")

    def invoke(
        self,
        value: str,
        *,
        sleep: float = 0,
        exit_code: int = 0,
        timeout: int = 10,
        extra_arguments: list[str] | None = None,
    ):
        harness = self.work / "invoke-helper.ps1"
        arguments = [str(self.child), "--value", value, "--sleep", str(sleep), "--exit-code", str(exit_code)]
        arguments.extend(extra_arguments or [])
        ps_arguments = ", ".join(self._ps_literal(item) for item in arguments)
        harness.write_text(
            textwrap.dedent(
                f"""
                $ErrorActionPreference = 'Stop'
                . {self._ps_literal(str(HELPER))}
                $result = Invoke-TradingOSChildProcess `
                    -FilePath {self._ps_literal(sys.executable)} `
                    -ArgumentList @({ps_arguments}) `
                    -WorkingDirectory {self._ps_literal(str(self.work))} `
                    -StdoutPath {self._ps_literal(str(self.stdout))} `
                    -StderrPath {self._ps_literal(str(self.stderr))} `
                    -TimeoutSeconds {timeout}
                $result | ConvertTo-Json -Compress
                """
            ).strip()
            + "\n",
            encoding="utf-8-sig",
        )
        completed = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            cwd=self.work,
            text=True,
            encoding="utf-8-sig",
            capture_output=True,
            timeout=timeout + 15,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(completed.stdout.strip())

    def test_unicode_spaces_exit_code_and_separate_logs(self):
        value = 'space ünicode "quote" and trailing\\'
        result = self.invoke(value, exit_code=7)
        self.assertTrue(result["Started"])
        self.assertFalse(result["TimedOut"])
        self.assertEqual(result["ExitCode"], 7)
        stdout_payload = json.loads(self.stdout.read_text(encoding="utf-8").strip())
        self.assertEqual(stdout_payload["value"], value)
        self.assertEqual(stdout_payload["python_utf8"], "1")
        self.assertEqual(stdout_payload["python_io_encoding"], "utf-8")
        self.assertEqual(stdout_payload["console_window"], 0)
        self.assertNotIn("stderr-", self.stdout.read_text(encoding="utf-8"))
        self.assertIn("stderr-" + value, self.stderr.read_text(encoding="utf-8"))

    def test_logs_append_instead_of_truncate(self):
        first = self.invoke("first")
        second = self.invoke("second")
        self.assertEqual(first["ExitCode"], 0)
        self.assertEqual(second["ExitCode"], 0)
        stdout = self.stdout.read_text(encoding="utf-8")
        self.assertIn('"value": "first"', stdout)
        self.assertIn('"value": "second"', stdout)

    def test_timeout_kills_child(self):
        result = self.invoke("timeout", sleep=5, timeout=1)
        self.assertTrue(result["TimedOut"])
        self.assertEqual(result["ExitCode"], 124)
        self._assert_pid_stops(result["ProcessId"])

    def test_high_volume_stdout_and_stderr_do_not_deadlock(self):
        result = self.invoke(
            "bulk",
            timeout=12,
            extra_arguments=["--stdout-bytes", "1048576", "--stderr-bytes", "1048576"],
        )
        self.assertEqual(result["ExitCode"], 0)
        self.assertFalse(result["StreamDrainTimedOut"])
        self.assertGreater(self.stdout.stat().st_size, 1_000_000)
        self.assertGreater(self.stderr.stat().st_size, 1_000_000)

    def test_timeout_kills_grandchild_tree(self):
        grandchild_pid_file = self.work / "timeout-grandchild.pid"
        result = self.invoke(
            "tree-timeout",
            sleep=30,
            timeout=1,
            extra_arguments=[
                "--spawn-grandchild-sleep",
                "30",
                "--grandchild-pid-file",
                str(grandchild_pid_file),
            ],
        )
        self.assertTrue(result["TimedOut"])
        self.assertEqual(result["ExitCode"], 124)
        self.assertTrue(grandchild_pid_file.exists())
        self._assert_pid_stops(result["ProcessId"])
        self._assert_pid_stops(int(grandchild_pid_file.read_text(encoding="ascii")))

    def test_exited_parent_with_inherited_pipe_is_bounded_and_tree_is_cleaned(self):
        grandchild_pid_file = self.work / "pipe-holder-grandchild.pid"
        started = time.monotonic()
        result = self.invoke(
            "pipe-holder",
            timeout=10,
            extra_arguments=[
                "--spawn-grandchild-sleep",
                "30",
                "--grandchild-pid-file",
                str(grandchild_pid_file),
            ],
        )
        elapsed = time.monotonic() - started
        self.assertFalse(result["TimedOut"])
        self.assertTrue(result["StreamDrainTimedOut"])
        self.assertEqual(result["ExitCode"], 125)
        self.assertLess(elapsed, 13)
        self.assertTrue(grandchild_pid_file.exists())
        self._assert_pid_stops(int(grandchild_pid_file.read_text(encoding="ascii")))

    def test_python_resolution_precedes_watchdog_lock_creation(self):
        source = WATCHDOG_LOOP.read_text(encoding="utf-8-sig")
        resolution = source.index("$Python = Get-PreferredPython")
        ownership = source.index("$Ownership = Enter-TradingOSLoopOwnership")
        self.assertLess(resolution, ownership)
        self.assertIn("-LockPath $LockPath", source[ownership:])
        self.assertIn("Requested Python executable is missing or is not a file", source)
        self.assertNotIn("Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue", source)

    def test_python_resolution_selects_one_concrete_executable(self):
        source = WATCHDOG_LOOP.read_text(encoding="utf-8-sig")
        start = source.index("function Get-PreferredPython")
        end = source.index("function Convert-ChildResultForStatus", start)
        resolver = source[start:end]
        self.assertIn("Select-Object -First 1", resolver)
        self.assertIn("return [string]$SystemPython.Source", resolver)

    def test_static_hidden_and_utf8_contract_cannot_be_overridden(self):
        source = HELPER.read_text(encoding="utf-8-sig")
        self.assertIn("$StartInfo.UseShellExecute = $false", source)
        self.assertIn("$StartInfo.CreateNoWindow = $true", source)
        self.assertIn("ProcessWindowStyle]::Hidden", source)
        environment_loop = source.index("foreach ($Name in $Environment.Keys)")
        mandatory_utf8 = source.index("$StartInfo.EnvironmentVariables['PYTHONUTF8'] = '1'", environment_loop)
        self.assertLess(environment_loop, mandatory_utf8)

    def test_loop_ownership_lock_is_utf8_atomic_and_released_by_owner(self):
        lock = self.work / "ownership.lock.json"
        completed = self.run_powershell(
            "ownership-single.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $lock = {self._ps_literal(str(lock))}
            $ownership = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'ownership_test' -LockPath $lock -ExpectedScriptPath $PSCommandPath
            $bytes = [System.IO.File]::ReadAllBytes($lock)
            $payload = Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
            $released = Exit-TradingOSLoopOwnership -Ownership $ownership -LockPath $lock
            [ordered]@{{
                acquired = [bool]$ownership.Acquired
                released = [bool]$released
                lock_exists_after = Test-Path -LiteralPath $lock
                owner_matches = ([string]$payload.owner_token -eq [string]$ownership.OwnerToken)
                schema_version = [int]$payload.schema_version
                has_bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191)
            }} | ConvertTo-Json -Compress
            """,
        )
        result = json.loads(completed.stdout.strip())
        self.assertTrue(result["acquired"])
        self.assertTrue(result["released"])
        self.assertFalse(result["lock_exists_after"])
        self.assertTrue(result["owner_matches"])
        self.assertEqual(result["schema_version"], 2)
        self.assertFalse(result["has_bom"])

    def test_concurrent_loop_ownership_has_exactly_one_owner(self):
        lock = self.work / "concurrent.lock.json"
        ready = self.work / "owner.ready"
        owner_script = self.work / "ownership-owner.ps1"
        owner_script.write_text(
            textwrap.dedent(
                f"""
                $ErrorActionPreference = 'Stop'
                . {self._ps_literal(str(HELPER))}
                $ownership = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'concurrent_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
                [System.IO.File]::WriteAllText({self._ps_literal(str(ready))}, [string]$PID)
                Start-Sleep -Seconds 4
                $released = Exit-TradingOSLoopOwnership -Ownership $ownership -LockPath {self._ps_literal(str(lock))}
                [ordered]@{{ acquired = [bool]$ownership.Acquired; released = [bool]$released }} | ConvertTo-Json -Compress
                """
            ).strip()
            + "\n",
            encoding="utf-8-sig",
        )
        owner = subprocess.Popen(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(owner_script)],
            cwd=self.work,
            text=True,
            encoding="utf-8-sig",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not ready.exists():
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "first owner never acquired the lock")
            contender = self.run_powershell(
                "ownership-contender.ps1",
                f"""
                $ErrorActionPreference = 'Stop'
                . {self._ps_literal(str(HELPER))}
                $ownership = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'concurrent_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
                [ordered]@{{ acquired = [bool]$ownership.Acquired; existing_pid = [int]$ownership.ExistingPid }} | ConvertTo-Json -Compress
                """,
            )
            contender_result = json.loads(contender.stdout.strip())
            self.assertFalse(contender_result["acquired"])
            owner_stdout, owner_stderr = owner.communicate(timeout=10)
            self.assertEqual(owner.returncode, 0, owner_stderr)
            owner_result = json.loads(owner_stdout.strip())
            self.assertTrue(owner_result["acquired"])
            self.assertTrue(owner_result["released"])
            self.assertFalse(lock.exists())
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(timeout=5)

    def test_owner_does_not_delete_replaced_lock(self):
        lock = self.work / "replaced.lock.json"
        completed = self.run_powershell(
            "ownership-replaced.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $lock = {self._ps_literal(str(lock))}
            $ownership = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'replace_test' -LockPath $lock -ExpectedScriptPath $PSCommandPath
            $foreign = Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
            $foreign.owner_token = 'foreign-owner-token'
            $foreign.owner_guid = 'foreign-owner-token'
            Write-TradingOSUtf8JsonAtomic -Path $lock -Payload $foreign -Depth 8
            $released = Exit-TradingOSLoopOwnership -Ownership $ownership -LockPath $lock
            $after = Get-Content -LiteralPath $lock -Raw | ConvertFrom-Json
            [ordered]@{{ released = [bool]$released; lock_exists = Test-Path -LiteralPath $lock; owner_token = [string]$after.owner_token }} | ConvertTo-Json -Compress
            """,
        )
        result = json.loads(completed.stdout.strip())
        self.assertFalse(result["released"])
        self.assertTrue(result["lock_exists"])
        self.assertEqual(result["owner_token"], "foreign-owner-token")

    def test_malformed_existing_lock_fails_closed_without_replacement(self):
        lock = self.work / "malformed.lock.json"
        original = b'{"pid": not-json'
        lock.write_bytes(original)
        completed = self.run_powershell(
            "ownership-malformed.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $threw = $false
            try {{
                $null = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'malformed_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
            }} catch {{ $threw = $true }}
            [ordered]@{{ threw = $threw; lock_exists = Test-Path -LiteralPath {self._ps_literal(str(lock))} }} | ConvertTo-Json -Compress
            """,
        )
        result = json.loads(completed.stdout.strip())
        self.assertTrue(result["threw"])
        self.assertTrue(result["lock_exists"])
        self.assertEqual(lock.read_bytes(), original)

    def test_live_unrelated_pid_lock_is_not_accepted_or_replaced(self):
        lock = self.work / "unrelated-live-pid.lock.json"
        original = json.dumps({"pid": os.getpid(), "root": str(self.work)}).encode("utf-8")
        lock.write_bytes(original)
        completed = self.run_powershell(
            "ownership-unrelated-live-pid.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $threw = $false
            try {{
                $null = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'unrelated_pid_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
            }} catch {{ $threw = $true }}
            [ordered]@{{ threw = $threw; lock_exists = Test-Path -LiteralPath {self._ps_literal(str(lock))} }} | ConvertTo-Json -Compress
            """,
        )
        result = json.loads(completed.stdout.strip())
        self.assertTrue(result["threw"])
        self.assertTrue(result["lock_exists"])
        self.assertEqual(lock.read_bytes(), original)

    def test_lock_write_failure_releases_mutex_for_retry(self):
        blocker = self.work / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        lock = blocker / "ownership.lock.json"
        failed = self.run_powershell(
            "ownership-write-failure.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $threw = $false
            try {{
                $null = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'write_failure_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
            }} catch {{ $threw = $true }}
            [ordered]@{{ threw = $threw }} | ConvertTo-Json -Compress
            """,
        )
        self.assertTrue(json.loads(failed.stdout.strip())["threw"])
        blocker.unlink()
        blocker.mkdir()
        retried = self.run_powershell(
            "ownership-write-retry.ps1",
            f"""
            $ErrorActionPreference = 'Stop'
            . {self._ps_literal(str(HELPER))}
            $ownership = Enter-TradingOSLoopOwnership -Root {self._ps_literal(str(self.work))} -ComponentId 'write_failure_test' -LockPath {self._ps_literal(str(lock))} -ExpectedScriptPath $PSCommandPath
            $released = Exit-TradingOSLoopOwnership -Ownership $ownership -LockPath {self._ps_literal(str(lock))}
            [ordered]@{{ acquired = [bool]$ownership.Acquired; released = [bool]$released }} | ConvertTo-Json -Compress
            """,
        )
        result = json.loads(retried.stdout.strip())
        self.assertTrue(result["acquired"])
        self.assertTrue(result["released"])

    def test_shutdown_after_health_skips_incident_and_releases_lock(self):
        root, loop = self.prepare_isolated_watchdog_runtime(health_requests_shutdown=True)
        completed = self.run_isolated_watchdog(loop)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((root / "health.marker").exists())
        self.assertFalse((root / "incident.marker").exists())
        lock = root / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop.lock.json"
        status = root / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop_status.json"
        self.assertFalse(lock.exists())
        payload = json.loads(status.read_text(encoding="utf-8"))
        self.assertEqual(payload["stop_reason"], "shutdown_gate_closed_after_health")
        self.assertEqual(payload["health_result"]["exit_code"], 0)
        self.assertIsNone(payload["incident_alert_result"])

    def test_infrastructure_exit_125_fail_stops_before_incident(self):
        root, loop = self.prepare_isolated_watchdog_runtime(health_exit=125)
        completed = self.run_isolated_watchdog(loop)
        self.assertEqual(completed.returncode, 70, completed.stderr)
        self.assertTrue((root / "health.marker").exists())
        self.assertFalse((root / "incident.marker").exists())
        lock = root / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop.lock.json"
        status = root / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop_status.json"
        self.assertFalse(lock.exists())
        payload = json.loads(status.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "stopped_after_health_infrastructure_failure")
        self.assertEqual(payload["exit_code"], 70)
        self.assertEqual(payload["health_result"]["exit_code"], 125)

    def test_business_failures_still_run_incident_and_preserve_codes(self):
        root, loop = self.prepare_isolated_watchdog_runtime(
            health_exit=1,
            incident_exit=2,
            incident_requests_shutdown=True,
        )
        completed = self.run_isolated_watchdog(loop)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((root / "health.marker").exists())
        self.assertTrue((root / "incident.marker").exists())
        status = root / "logs" / "cex_dex_funding" / "cex_dex_funding_freshness_watchdog_loop_status.json"
        payload = json.loads(status.read_text(encoding="utf-8"))
        self.assertEqual(payload["stop_reason"], "shutdown_gate_closed_after_incident")
        self.assertEqual(payload["health_exit_code"], 1)
        self.assertEqual(payload["incident_alert_exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
