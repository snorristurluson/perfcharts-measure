import argparse
import copy
import json
import os
import platform
import shlex
import subprocess
import time
import urllib
import urllib.request

import psutil as psutil
import yaml


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


class Measurer:
    def __init__(self, config, branch):
        self.config = config
        self.branch = branch

    def run(self, count):

        if not os.path.exists(self.config["folder"]):
            os.mkdir(self.config["folder"])
        if not os.path.exists(os.path.join(self.config["folder"], ".git")):
            self.cmd("git clone {repoUrl} {folder}".format(**self.config), run_in_folder=False)

        self.cmd("git fetch && git checkout {branch} && git pull".format(branch=self.branch))

        cmd = "git rev-list --max-count {count} --first-parent {branch}".format(branch=self.branch, count=count)
        shas = self.cmd(cmd, capture_output=True).strip()
        sha_list = shas.split("\n")

        for sha in reversed(sha_list):
            cmd = "git show --pretty=format:'%ae%n%aI%n%s%n%b' " + sha
            lines = self.cmd(cmd, capture_output=True).split("\n")
            revision = {
                "repo": self.config["repoName"],
                "branch": self.branch,
                "sha": sha,
                "author": lines[0],
                "date": lines[1],
                "title": lines[2],
                "message": "\n".join(lines[3:])
            }
            post_data(revision, "revision")

            print("Building {sha}".format(sha=sha))
            self.cmd("git checkout {sha}".format(sha=sha))

            self.cmd(self.config["build"])

            try:
                output_files = measure(sha, self.config["repoName"], self.branch, self.config["folder"],
                                       self.config["benchmarks"])
            except Exception as e:
                print(e)

            # todo: check output against reference

    def cmd(self, cmd, capture_output=False, run_in_folder=True):
        if run_in_folder:
            cmd = "cd {folder} && {cmd}".format(folder=self.config["folder"], cmd=cmd)

        print(cmd)
        result = subprocess.run(cmd, shell=True, capture_output=capture_output)
        if result.returncode != 0:
            print("command failed - {}".format(result.returncode))
            exit(1)
        if capture_output:
            return result.stdout.decode("utf8")


def measure(sha, repo, branch, cwd, benchmarks):
    base = {
        "commitid": sha,
        "repo": repo,
        "branch": branch,
        "environment": platform.node(),
    }

    data_list = []

    files = []
    for each in benchmarks:
        exe_name = each["executable"]
        benchmark_name = each["name"]
        print("Running {}:{}".format(exe_name, benchmark_name))

        outname = "{exe}_{name}_{sha}_stdout.txt".format(sha=sha, exe=exe_name, name=benchmark_name)
        errname = "{exe}_{name}_{sha}_stderr.txt".format(sha=sha, exe=exe_name, name=benchmark_name)

        files.append(outname)
        files.append(errname)

        out = open(outname, "w")
        err = open(errname, "w")

        cmd_and_args = shlex.split(each["command"])
        result = run_benchmark(cmd_and_args, cwd, out, err)

        package_data(result, base, exe_name, benchmark_name, data_list)

    post_data(data_list, "result")

    return files


def package_data(result, base, exe_name, benchmark_name, data_list):
    for field in result.keys():
        data = copy.copy(base)
        data["executable"] = exe_name
        data["benchmark"] = benchmark_name
        data["metric"] = field
        data["result_value"] = result[field]

        data_list.append(data)


def post_data(data, endpoint):
    as_json = json.dumps(data).encode("utf8")
    try:
        url = "http://localhost:8123/%s/" % endpoint
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        request = urllib.request.Request(url, data=as_json, headers=headers)
        with urllib.request.urlopen(request) as f:
            response = f.read()
            print(response)
    except urllib.request.HTTPError as e:
        print(e)


def run_benchmark(cmd_and_args, cwd, out, err):
    samples = []
    fields = ["cpu_times", "memory_info", "num_threads", "num_fds"]

    start = time.perf_counter()
    with psutil.Popen(cmd_and_args, cwd=cwd, stdout=out, stderr=err, text=True) as p:
        gather_samples(fields, p, samples)
    end = time.perf_counter()
    duration = end - start

    rss = max([x["memory_info"].rss for x in samples])
    user = samples[-1]["cpu_times"].user
    sys = samples[-1]["cpu_times"].system

    result = {
        "duration": duration,
        "rss": rss,
        "usr": user,
        "sys": sys
    }
    return result


def gather_samples(fields, p, samples):
    while p.poll() is None:
        if p.is_running():
            sample = p.as_dict(fields)
            if validate_sample(sample):
                samples.append(sample)
            time.sleep(0.1)


def validate_sample(sample):
    sample_is_good = True
    if sample is None:
        sample_is_good = False
    else:
        for each in sample.values():
            if each is None:
                sample_is_good = False
                break
    return sample_is_good


if __name__ == "__main__":
    main()
