import argparse

import yaml

from measurer import Measurer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="Configuration file")
    parser.add_argument("--branch", help="Branch to build", default="master")
    parser.add_argument("--count", help="Number of commits, starting with the latest", default=1, type=int)
    parser.add_argument("--reference", help="Set results as reference values for benchmarks run", action="store_true")
    parser.add_argument("--nobuild", help="Assume executable is already built", action="store_true")

    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.full_load(f)

    m = Measurer(config, args)
    m.run(args.count)


if __name__ == "__main__":
    main()
