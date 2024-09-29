import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests as requests
from lxml import etree
from slack_sdk import WebClient

from jobs import get_periodic_jobs

TESTS_PREFIX = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/"
DELTA_TIME_HOURS = os.getenv("DELTA_TIME_HOURS", 24)

versions = []
platforms = [
    "metal",
    "aws",
    "azure",
]

periodic_jobs = []

jobs_map = {}
blocks_data_original = []
blocks_data = []

class ProwJob:
    full_name : str
    base_url : str
    version: str
    platform: str
    variant: str
    executions: list

    def __init__(self, full_name):
        self.full_name = full_name
        for platform in platforms:
            if platform in self.full_name:
                self.platform = platform

        self.variant = self.full_name.split("periodics-")[1]

        pattern = r'\d\.\d+'
        match = re.search(pattern, self.full_name)
        if match:
            self.version = match[0]
            if self.version not in versions:
                versions.append(self.version)
        else:
            raise Exception(f"a version is missing at {self.full_name}")

        self.executions = []


class JobRun:
    id: str
    timestamp: str
    job_url: str
    result: str

    def __init__(self, id, timestamp, job_url, result):
        self.id = id
        self.timestamp = timestamp
        self.job_url = job_url
        self.result = result


def set_up_jobs():
    jobs = get_periodic_jobs()
    for j in jobs:
        pj = ProwJob(j)
        periodic_jobs.append(pj)


def collect_data():
    for pj in periodic_jobs:
        html_response = requests.get(TESTS_PREFIX + pj.full_name).text
        tree = etree.HTML(html_response)
        all_elements = reversed(list(tree.iter()))
        for el in all_elements:
            if el.tag == 'img' and '..' not in el.tail and 'latest-build' not in el.tail:
                job_id = el.tail.strip()

                prowjob_response = requests.get(TESTS_PREFIX + pj.full_name + '/' + job_id + "prowjob.json").text
                prowjob = json.loads(prowjob_response)
                if "completionTime" not in prowjob["status"]:
                    continue
                timestamp = prowjob["status"]["completionTime"]
                if before_delta(timestamp):
                    break
                test_result = prowjob["status"]["state"]
                job_url = prowjob["status"]["url"] if "url" in prowjob["status"] else "N/A"

                jr = JobRun(job_id.replace('/', ''), timestamp, job_url, test_result)
                pj.executions.append(jr)

                print (f'{pj.full_name} from {jr.timestamp} parsed. Result: {jr.result.split(' ')[0]}')
    print ("Done.")


def organize_data():
    for pj in sorted(periodic_jobs, key=lambda pj: pj.version, reverse=True):
        if len(pj.executions) == 0:
            continue
        if pj.version not in jobs_map:
            jobs_map[pj.version] = {}
        if pj.platform not in jobs_map[pj.version]:
            jobs_map[pj.version][pj.platform] = {}

        if pj.variant not in jobs_map[pj.version][pj.platform]:
            jobs_map[pj.version][pj.platform][pj.variant] = []

        jobs_map[pj.version][pj.platform][pj.variant] = pj.executions


def build_blocks():
    blocks = {
        "blocks": [
            {
                "type": "rich_text",
                "elements": []
            }
        ]
    }

    for version in jobs_map:
        blocks["blocks"][0]["elements"].append(rtl(version, 0))
        for platform in jobs_map[version]:
            blocks["blocks"][0]["elements"].append(rtl(platform, 1, bold=True))
            for variant in jobs_map[version][platform]:
                blocks["blocks"][0]["elements"].append(rtl(f"{variant}: ", 2))
                add_results_sections(blocks["blocks"][0]["elements"][-1]["elements"][0]["elements"], jobs_map[version][platform][variant])

    return blocks["blocks"]


def add_results_sections(rtl_block, executions):
    for execution in executions:
        result_element = {
            "type": "link",
            "url": execution.job_url,
            "text": execution.result + " "
        }
        emoji = "solid-success" if execution.result == "success" else "failed"
        emoji_element = {
            "type": "emoji",
            "name": emoji
        }
        rtl_block.append(result_element)
        rtl_block.append(emoji_element)
        if executions.index(execution) < len(executions) - 1:
            rtl_block.append(comma_section())


def comma_section():
    return {"type": "text", "text": ", "}


def rts(text):
    # rich text section
    return {"type": "text", "text": text}


def rtl(text, indent, bold=False):
    # rich text list
    return {
        "type": "rich_text_list",
        "style": "bullet",
        "indent": indent,
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [
                    {
                        "type": "text",
                        "text": text,
                        "style": {
                            "bold": bold
                        }
                    }
                ]
            }
        ]
    }


def compose_summary_message():
    for version in jobs_map:
        message = f"• {version}:\n"
        for platform in jobs_map[version]:
            message += f"    • *{platform}*:\n"
            for variant in jobs_map[version][platform]:
                results = [f"<{execution.job_url}|{execution.result}>" for execution in jobs_map[version][platform][variant]]
                message += f"        - {variant}: " + ', '.join(results) + '\n'
        blocks_data_original.append(message)


def shrink_blocks():
    new_block = ""
    i = 0
    for block in blocks_data_original:
        for line in block.splitlines():
            new_block += line + '\n'
            if i >= 4:
                blocks_data.append(new_block)
                new_block = ""
                i = 0
                continue
            i += 1


def post_on_slack():
    oauth_token = os.getenv("OAUTH_TOKEN")
    if os.getenv("DEVELOPMENT") == "true":
        channel_id = os.getenv("CHANNEL_ID_PRIV")
    else:
        channel_id = os.getenv("CHANNEL_ID")
    if not channel_id:
        raise Exception("CHANNEL_ID has not been provided")

    client = WebClient(token=oauth_token)

    sum_results = get_summary_results()
    summary_per_v = get_summary_per_version(sum_results)
    title = f"HyperShift-KubeVirt periodics summary: {sum_results["total_passed"]} out of {sum_results["total"]} jobs passed in total. Details per version: {summary_per_v}"

    response = client.chat_postMessage(
        channel=channel_id,
        text=title,
    )
    message_ts = response['ts']
    blocks = build_blocks()

    detailed_results_response = client.chat_postMessage(
        channel=channel_id,
        thread_ts=message_ts,
        text="details:",
        blocks=blocks,
    )


def get_summary_results():
    sum_results = {}
    sum_results["total"] = 0
    sum_results["total_passed"] = 0

    for version in jobs_map:
        sum_results[version] = {}
        sum_results[version]["total"] = 0
        sum_results[version]["passed"] = 0
        for platform in jobs_map[version]:
            for variant in jobs_map[version][platform]:
                for execution in jobs_map[version][platform][variant]:
                    sum_results["total"] += 1
                    sum_results[version]["total"] += 1
                    if execution.result.startswith("success"):
                        sum_results["total_passed"] += 1
                        sum_results[version]["passed"] += 1

    return sum_results


def get_summary_per_version(sum_results):
    summary_per_v = ""
    count = 0
    for key, version in sum_results.items():
        count += 1
        if not isinstance(version, dict):
            continue

        summary_per_v += f"{key}: {version["passed"]}/{version["total"]}"
        if count < len(sum_results):
            summary_per_v += ", "

    return summary_per_v


def before_delta(timestamp_str):
    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
    timestamp = timestamp.replace(tzinfo=timezone.utc)
    max_valid_time = datetime.now(tz=timezone.utc) - timedelta(hours=int(DELTA_TIME_HOURS))

    return timestamp <= max_valid_time


def job_exists(job_id, test_name, test_jobs):
    for job in test_jobs[test_name]:
        if job["job_id"] == job_id.replace('/', '') and job["result"] != "pending":
            return True
    return False


def create_dirs_if_not_exists(dirs):
    for dir in dirs:
        if not os.path.exists(dir):
            os.makedirs(dir)

def main():
    set_up_jobs()
    collect_data()
    organize_data()
    post_on_slack()


if __name__ == '__main__':
    main()

