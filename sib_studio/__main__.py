"""python -m sib_studio          -> opens the studio
python -m sib_studio cli ...  -> command line tools (see sib_studio/core/cli.py)"""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from .core.cli import main as cli_main
    cli_main(sys.argv[2:])
else:
    from .app import main
    main()
