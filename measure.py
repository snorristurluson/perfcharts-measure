import argparse

import yaml

from measurer import Measurer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="Configuration file")
    parser.add_argument("branch", help="Branch to build")
    parser.add_argument("--count", help="Number of commits, starting with the latest", default=1, type=int)

    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.full_load(f)

    m = Measurer(config, args.branch)
    m.run(args.count)


if __name__ == "__main__":
    main()
