#!/usr/bin/python3

"""Release bot"""

import argparse
import subprocess
import sys
import os
import time
import logging
from datetime import date
from ghapi.all import GhApi


class fg:  # pylint: disable=too-few-public-methods
    """Set of constants to print colored output in the terminal"""
    BOLD = '\033[1m'  # bold
    OK = '\033[32m'  # green
    INFO = '\033[33m'  # yellow
    ERROR = '\033[31m'  # red
    RESET = '\033[0m'  # reset


def msg_error(body):
    """Print error and exit"""
    print(f"{fg.ERROR}{fg.BOLD}Error:{fg.RESET} {body}")
    sys.exit(1)


def msg_info(body):
    """Print info message"""
    print(f"{fg.INFO}{fg.BOLD}Info:{fg.RESET} {body}")


def msg_ok(body):
    """Print ok status message"""
    print(f"{fg.OK}{fg.BOLD}OK:{fg.RESET} {body}")


def run_command(argv):
    """Run a shellcommand and return stdout"""
    result = subprocess.run(  # pylint: disable=subprocess-run-check
        argv,
        capture_output=True,
        text=True,
        encoding='utf-8')

    if result.returncode == 0:
        ret = result.stdout.strip()
    else:
        ret = result.stderr.strip()

    return ret


def autoincrement_version(latest_tag):
    """Bump the version of the latest git tag by 1"""
    if latest_tag == "":
        msg_info("There are no tags yet in this repository.")
        version = "1"
    elif "." in latest_tag:
        version = latest_tag.replace("v", "").split(".")[0] + "." + str(int(latest_tag[-1]) + 1)
    else:
        version = int(latest_tag.replace("v", "")) + 1
    return version


def list_prs_for_hash(args, api, repo, commit_hash):
    """Get pull request for a given commit hash"""
    query = f'{commit_hash} type:pr is:merged base:{args.base} repo:osbuild/{repo}'
    try:
        res = api.search.issues_and_pull_requests(q=query, per_page=20)
    except:
        msg_info(f"Couldn't get PR infos for {commit_hash}.")
        res = None

    if res is not None:
        items = res["items"]

        if len(items) == 1:
            ret = items[0]
        else:
            msg_info(f"There are {len(items)} pull requests associated with {commit_hash} - skipping...")
            for item in items:
                msg_info(f"{item.html_url}")
            ret = None
    else:
        ret = None

    return ret


def get_pullrequest_infos(args, repo, hashes):
    """Fetch the titles of all related pull requests"""
    logging.debug("Collect pull request titles...")
    api = GhApi(repo=repo, owner='osbuild', token=args.token)
    summaries = []

    for i, commit_hash in enumerate(hashes):
        print(f"Fetching PR for commit {i}/{len(hashes)} ({commit_hash})")
        time.sleep(2)
        pull_request = list_prs_for_hash(args, api, repo, commit_hash)
        if pull_request is not None:
            if repo == "cockpit-composer":
                msg = f"- {pull_request.title} (#{pull_request.number})"
            else:
                msg = f"  * {pull_request.title} (#{pull_request.number})"
            summaries.append(msg)

    summaries = list(dict.fromkeys(summaries)) # Deduplicate the list of pr summaries
    msg_ok(f"Collected summaries from {len(summaries)} pull requests ({i} commits).")
    return "\n".join(summaries)


def get_contributors(latest_tag):
    """Collect all contributors to a release based on the git history"""
    logging.debug("Collect contributors...")
    contributors = run_command(["git", "log", '--format="%an"', f"{latest_tag}..HEAD"])
    contributor_list = contributors.replace('"', '').split("\n")
    contributor_list.pop() # Remove the author of the post release version bump commit
    names = ""
    for name in sorted(set(contributor_list)):
        if name != "":
            names += f"{name}, "

    logging.debug(f"List of contributors:\n{names[:-2]}")

    return names[:-2]


def create_release_tag(args, repo, tag, latest_tag):
    """Create a release tag"""
    logging.debug("Preparing tag...")
    today = date.today()
    contributors = get_contributors(latest_tag)

    summaries = ""
    hashes = run_command(['git', 'log', '--format=%H', f'{latest_tag}..HEAD']).split("\n")
    msg_info(f"Found {len(hashes)} commits since {latest_tag} in {args.base}:")
    logging.debug("\n".join(hashes))
    summaries = get_pullrequest_infos(args, repo, hashes)

    tag = f'v{args.version}'
    message = (f"CHANGES WITH {args.version}:\n\n"
            f"----------------\n"
            f"{summaries}\n\n"
            f"Contributions from: {contributors}\n\n"
            f"— Somewhere on the Internet, {today.strftime('%Y-%m-%d')}")

    res = run_command(['git', 'tag', '-m', message, tag, 'HEAD'])
    logging.debug(res)
    msg_ok(f"Created tag '{tag}' with message:\n{message}")


def print_config(args, repo):
    """Print the values used for the release playbook"""
    print("\n--------------------------------\n"
          f"{fg.BOLD}Release:{fg.RESET}\n"
          f"  Component:     {repo}\n"
          f"  Version:       {args.version}\n"
          f"  Base branch:   {args.base}\n"
          f"--------------------------------\n")


def main():
    """Main function"""
    # Get some basic fallback/default values
    repo = os.path.basename(os.getcwd())
    latest_tag = run_command(['git', 'describe', '--tags', '--abbrev=0'])
    version = autoincrement_version(latest_tag)

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version",
                        help=f"Set the version for the release (Default: {version})",
                        default=version)
    parser.add_argument("-t", "--token", help=f"Set the GitHub token")
    parser.add_argument("-b", "--base",
                        help=f"Set the release branch (Default: 'main')",
                        default='main')
    parser.add_argument("-d", "--debug", help="Print lots of debugging statements", action="store_const",
                        dest="loglevel", const=logging.DEBUG, default=logging.INFO)
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel, format='%(asctime)s %(message)s', datefmt='%Y/%m/%d/ %H:%M:%S')

    print_config(args, repo)

    tag = f'v{args.version}'
    logging.debug(f"Current release: {latest_tag}\nNew release: {args.version}\nTag name: {tag}")

    # Create a release tag
    create_release_tag(args, repo, tag, latest_tag)

    # Push the tag
    res = run_command(['git', 'push', 'origin', tag])
    logging.debug(res)
    msg_ok(f"Pushed tag '{tag}' to branch '{args.base}'")


if __name__ == "__main__":
    main()
