#!/usr/bin/env python3
"""hello_nyrqis — Hello World for Nyrqis.

A minimal Nyrqis application that prints a greeting and exits.
This is the "Hello World" of the Nyrqis platform.

Usage::

    # Build as .napp
    python3 tools/nyapp.py build --name hello-nyrqis --source examples/hello_nyrqis.py --output hello.napp

    # Run the .napp
    python3 tools/nyapp.py run hello.napp

    # Or run directly
    python3 examples/hello_nyrqis.py
"""

print("Hello from Nyrqis! 🍄")
print("The mushroom operating system")
state["greeting"] = "hello"
state["platform"] = "nyrqis"
