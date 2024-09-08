import os
import re

from git import Repo

PROW_JOBS_REPO = "https://github.com/openshift/release.git"
REPO_PATH = "jobs_repo"
HYPERSHIFT_JOBS_PATH = "ci-operator/jobs/openshift/hypershift"

def get_periodic_jobs():
    full_jobs_path = os.path.join(REPO_PATH, HYPERSHIFT_JOBS_PATH)
    jobs = []
    clone_repo()
    periodic_jobs_files = get_periodic_jobs_files(full_jobs_path)

    pattern = r'name: (.*kubevirt.*$)'
    for pjf in periodic_jobs_files:
        with open(os.path.join(full_jobs_path, pjf), 'r') as fh:
            file_contents = fh.readlines()
            for line in file_contents:
                match = re.match(pattern, line.strip())
                if match:
                    jobs.append(match.group(1))

    return jobs


def clone_repo():
    if not os.path.exists(REPO_PATH):
        Repo.clone_from(PROW_JOBS_REPO, REPO_PATH, branch="master")

    jobs_repo = Repo(REPO_PATH)
    for remote in jobs_repo.remotes:
        remote.fetch()
    jobs_repo.git.checkout('master')


def get_periodic_jobs_files(full_jobs_path):
    periodic_files = []
    for file in os.listdir(full_jobs_path):
        if file.endswith("periodics.yaml"):
            periodic_files.append(file)
    return periodic_files

