"""Probe: what does the manager receive for the command pid, and why."""
import os, sys, time

sys.path.insert(0, ".")
from backend.container import ContainerManager, ContainerConfig

m = ContainerManager(use_cgroups_v2=False, use_direct_syscalls=True)
c = m.create(ContainerConfig(command=["/bin/sleep", "60"], seccomp=False))
m.spawn(c)
time.sleep(1.5)

print("manager container.pid   =", c.pid)
print("manager container._init =", c._init_pid)
init = c._init_pid
if init:
    print(f"--- /proc/{init}/task/{init}/children ---")
    try:
        print(repr(open(f"/proc/{init}/task/{init}/children").read()))
    except OSError as e:
        print("children read failed:", e)
    try:
        print("children stat mode:", oct(os.stat(f"/proc/{init}/task/{init}/children").st_mode))
    except OSError as e:
        print("children stat failed:", e)
if c.pid:
    try:
        with open(f"/proc/{c.pid}/status") as fh:
            for line in fh:
                if line.startswith(("Pid:", "PPid:", "NSpid:")):
                    print(f"/proc/{c.pid} {line.rstrip()}")
    except OSError as e:
        print(f"read /proc/{c.pid}/status: {e}")
m.terminate(c)
m._cleanup_policy_files()
print("done")
