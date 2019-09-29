import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
import urllib
import urllib.request

import psutil as psutil


def calc_checksum(outname):
    m = hashlib.md5()
    with open(outname) as f:
        for chunk in iter(f.read(4096)):
            m.update(chunk.encode("utf8"))
    checksum = m.hexdigest()
    return checksum


class Measurer:
    def __init__(self, config, args):
        self.output_files = []
        self.config = config
        self.branch = args.branch
        self.set_reference = args.reference
        self.skip_build = args.nobuild
        if self.skip_build and self.set_reference:
            self.branch = "specialReferenceRevisionBranch"

    def run(self, count):
        self.prepare_repo_folder()
        revision_list = self.get_revisions(count)

        if not revision_list:
            print("No revisions found", sys.stderr)

        for revision in revision_list:
            revision_details = self.get_revision_details(revision)
            self.post_data(revision_details, "revision")

        for revision in revision_list:
            self.build_revision(revision)
            self.run_benchmarks_for_revision(revision)

    def run_benchmarks_for_revision(self, revision):
        try:
            working_directory = self.config["folder"]
            benchmarks = self.config["benchmarks"]
            for each in benchmarks:
                results_list = []
                metrics, checksum = self.run_benchmark(each, revision, working_directory)

                for field, value in metrics.items():
                    data = {
                        "commitid": revision,
                        "repo": self.config["repoName"],
                        "branch": self.branch,
                        "environment": platform.node(),
                        "executable": (each["executable"]),
                        "benchmark": (each["name"]),
                        "metric": field,
                        "result_value": value,
                        "checksum": checksum
                    }

                    results_list.append(data)
                print("Posting results for {name}:{executable}".format(**each))
                self.post_data(results_list, "result")

        except Exception as e:
            print(e)

    def run_benchmark(self, benchmark, revision, working_directory):
        name = benchmark["name"]
        exe = benchmark["executable"]

        print("Running {}:{}".format(exe, name))

        out_name = "{exe}_{name}_{sha}_stdout.txt".format(sha=revision, exe=exe, name=name)
        err_name = "{exe}_{name}_{sha}_stderr.txt".format(sha=revision, exe=exe, name=name)
        self.output_files.append(out_name)
        self.output_files.append(err_name)
        with open(out_name, "w") as out, open(err_name, "w") as err:
            cmd_and_args = shlex.split(benchmark["command"])
            result = self.measure_benchmark(cmd_and_args, err, out, working_directory)

        checksum = self.archive_output(out_name)
        if os.path.getsize(err_name) == 0:
            os.remove(err_name)

        return result, checksum

    @staticmethod
    def archive_output(outname):
        checksum = calc_checksum(outname)
        filename = checksum + ".txt"
        if not os.path.exists(filename):
            shutil.move(outname, filename)
        else:
            os.remove(outname)
        return checksum

    def measure_benchmark(self, cmd_and_args, err, out, working_directory):
        samples = []
        fields = ["cpu_times", "memory_info", "num_threads", "num_fds"]
        start = time.perf_counter()
        with psutil.Popen(cmd_and_args, cwd=working_directory, stdout=out, stderr=err, text=True) as p:
            self.measure_process(p, fields, samples)
        end = time.perf_counter()
        duration = end - start
        rss = max([x["memory_info"].rss for x in samples])
        user = samples[-1]["cpu_times"].user
        system = samples[-1]["cpu_times"].system
        result = {
            "duration": duration,
            "rss": rss,
            "usr": user,
            "sys": system
        }
        return result

    @staticmethod
    def measure_process(p, fields, samples):
        while p.poll() is None:
            if p.is_running():
                sample = p.as_dict(fields)
                sample_is_good = True
                if sample is None:
                    sample_is_good = False
                else:
                    for each1 in sample.values():
                        if each1 is None:
                            sample_is_good = False
                            break
                if sample_is_good:
                    samples.append(sample)
                time.sleep(0.1)

    def build_revision(self, sha):
        if not self.skip_build:
            print("Building {sha}".format(sha=sha))
            self.cmd("git checkout {sha}".format(sha=sha))
            self.cmd(self.config["build"])

    def get_revision_details(self, sha):
        if self.skip_build:
            revision = {
                "repo": self.config["repoName"],
                "branch": self.branch,
                "sha": self.config["revision"]["name"],
                "date": self.config["revision"]["date"],
                "title": "Reference revision {}".format(self.config["revision"]["name"]),
                "message": "Reference revision {}".format(self.config["revision"]["name"])
            }
        else:
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
        return revision

    def get_revisions(self, count):
        if self.skip_build:
            revision_list = [self.config["revision"]["name"]]
        else:
            cmd = "git rev-list --max-count {count} --first-parent {branch}".format(branch=self.branch, count=count)
            revisions = self.cmd(cmd, capture_output=True).strip()
            descending_revision_list = revisions.split("\n")
            revision_list = list(reversed(descending_revision_list))
        return revision_list

    def prepare_repo_folder(self):
        if self.skip_build:
            return

        if not os.path.exists(self.config["folder"]):
            os.mkdir(self.config["folder"])
        if not os.path.exists(os.path.join(self.config["folder"], ".git")):
            self.cmd("git clone {repoUrl} {folder}".format(**self.config), run_in_folder=False)
        self.cmd("git fetch && git checkout {branch} && git pull".format(branch=self.branch))

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

    @staticmethod
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
                if response:
                    print(response.decode("utf8"))
        except urllib.request.HTTPError as e:
            print(e)
