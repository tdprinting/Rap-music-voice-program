"""Entry script used by PyInstaller and for running from source: python run_sib.py"""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from sib_studio.core.cli import main
    main(sys.argv[2:])
else:
    from sib_studio.app import main
    main()
