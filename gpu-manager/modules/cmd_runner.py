import subprocess
import sys

DEBUG_MODE = False

def run_cmd(cmd: list, check: bool = False, capture_output: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    global DEBUG_MODE
    if DEBUG_MODE:
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        print(f"[DEBUG] Executing: {cmd_str}", file=sys.stderr)

    res = subprocess.run(cmd, capture_output=capture_output, text=text)

    if DEBUG_MODE:
        print(f"[DEBUG] Return code: {res.returncode}", file=sys.stderr)
        if res.stdout and res.stdout.strip():
            print(f"[DEBUG] Stdout:\n{res.stdout.strip()}", file=sys.stderr)
        if res.stderr and res.stderr.strip():
            print(f"[DEBUG] Stderr:\n{res.stderr.strip()}", file=sys.stderr)

    if check and res.returncode != 0:
        raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)

    return res
