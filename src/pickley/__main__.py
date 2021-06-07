"""Allows to run via python -m pickley"""


def main():
    import runez

    from pickley.cli import main, SoftLockException

    runez.click.protected_main(main, no_stacktrace=[SoftLockException])


if __name__ == "__main__":
    main()
