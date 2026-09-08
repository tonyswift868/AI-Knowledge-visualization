"""Explicit seed-runtime packaging entry; never a product startup entry."""
from scripts.uat_base_seed import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error))
        raise SystemExit(1)
