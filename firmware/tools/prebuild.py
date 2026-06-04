#  PlatformIO pre-build hook: regenerate src/disk_image.h from ../client before
#  every build so the embedded USB drive always matches the current host client.
#  Referenced from platformio.ini as `extra_scripts = pre:tools/prebuild.py`.
import os
import subprocess

Import("env")  # noqa: F821  (injected by PlatformIO/SCons)

project_dir = env.subst("$PROJECT_DIR")  # noqa: F821
script = os.path.join(project_dir, "tools", "mkdisk.sh")
print("Geppetto: regenerating embedded client disk image...")
subprocess.check_call(["bash", script])
