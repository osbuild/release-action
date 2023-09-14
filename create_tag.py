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
from packaging.version import Version


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


def run_command(argv, check=False):
    """Run a shellcommand and return stdout"""
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=check)

    if result.returncode == 0:
        ret = result.stdout.strip()
    else:
        ret = result.stderr.strip()

    return ret


def autoincrement_version(latest_version, semver=False, semver_bump_type="major"):
    """
    Bumps the latest_version by one, depending on the type of versioning used.
    Returns the new version as a string.
    """
    if latest_version is None:
        if semver:
            if semver_bump_type == "major":
                return "1.0.0"
            elif semver_bump_type == "minor":
                return "0.1.0"
            elif semver_bump_type == "patch":
                return "0.0.1"
        return "1"

    version = Version(latest_version)
    if semver:
        if semver_bump_type == "minor":
            return f"{version.major}.{version.minor + 1}.0"
        elif semver_bump_type == "patch":
            return f"{version.major}.{version.minor}.{version.micro + 1}"
        else:
            return f"{version.major + 1}.0.0"
    # handle simple dot-releases by bumping the last number
    elif len(version.release) > 1:
        return ".".join([str(x) for x in version.release[:len(version.release) - 1]] + [str(version.release[len(version.release) - 1] + 1)])
    else:
        return str(version.major + 1)


def get_full_username(api, username):
    """
    Return the full name of a github user
    """
    user = api.users.get_by_username(username=username)

    return user['name']


def list_reviewers_for_pr(api, repo, pr_number):
    """
    Return all reviewers that approved a pull request
    """
    try:
        res = api.pulls.list_reviews(owner="osbuild",repo=repo, pull_number=pr_number, per_page=20)
    except:
        msg_info(f"Couldn't get reviewers for PR #{pr_number}.")
        res = None

    reviewers = []

    if res is not None:
        for item in res:
            if item['state'] == "APPROVED":
                reviewer = get_full_username(api, item['user']['login'])
                reviewers.append(reviewer)

    return sorted(set(reviewers))


def list_prs_for_hash(args, api, repo, commit_hash):
    """Get pull request for a given commit hash"""
    query = f'{commit_hash} type:pr is:merged base:{args.base} repo:osbuild/{repo}'
    try:
        res = api.search.issues_and_pull_requests(q=query, per_page=20)
    except:
        msg_info(f"Couldn't get PR infos for {commit_hash}.")
        res = None

    pull_request = None
    author = "Nobody"
    reviewers = ["Nobody"]

    if res is not None:
        items = res["items"]

        if len(items) == 1:
            pull_request = items[0]
            username = pull_request.user['login']
            author = get_full_username(api, username)
            reviewers = list_reviewers_for_pr(api, repo, pull_request.number)
        else:
            msg_info(f"There are {len(items)} pull requests associated with {commit_hash} - skipping...")
            for item in items:
                msg_info(f"{item.html_url}")

    return pull_request, author, reviewers


def get_pullrequest_infos(args, repo, hashes):
    """Fetch the titles of all related pull requests"""
    logging.debug("Collect pull request titles...")
    api = GhApi(repo=repo, owner='osbuild', token=args.token)
    summaries = []

    for i, commit_hash in enumerate(hashes):
        print(f"Fetching PR for commit {i+1}/{len(hashes)} ({commit_hash})")
        time.sleep(2)
        pull_request, author, reviewers = list_prs_for_hash(args, api, repo, commit_hash)
        if pull_request is not None:
            if repo == "cockpit-composer":
                msg = (f"- {pull_request.title} (#{pull_request.number})\n"
                       f"  - Author: {author}, Reviewers: {', '.join(reviewers)})")
            else:
                msg = (f"  * {pull_request.title} (#{pull_request.number})\n"
                       f"    * Author: {author}, Reviewers: {', '.join(reviewers)}")
            summaries.append(msg)

    # Deduplicate the list of pr summaries and sort it
    summaries = sorted(list(dict.fromkeys(summaries)))
    msg_ok(f"Collected summaries from {len(summaries)} pull requests ({i} commits).")
    return "\n".join(summaries)


def create_release_tag(args, repo, tag, latest_tag):
    """Create a release tag"""
    logging.debug("Preparing tag...")
    today = date.today()

    summaries = ""
    hashes = run_command(['git', 'log', '--format=%H', f'{latest_tag}..HEAD']).splitlines()
    msg_info(f"Found {len(hashes)} commits since {latest_tag} in {args.base}:")
    logging.debug("\n".join(hashes))

    subjects = run_command(['git', 'log', '--format=%s', f'{latest_tag}..HEAD']).splitlines()
    # Don't release when there are no changes
    if (len(hashes) < 1) or (len(subjects) == 1 and subjects[0] == "Post release version bump"):
        msg_info("No new commits have been pushed since the latest release (apart from the post release version bump) therefore skipping the tag.")
        sys.exit(0)

    summaries = get_pullrequest_infos(args, repo, hashes)

    tag = f'v{args.version}'
    message = (f"Changes with {args.version}\n\n"
            f"----------------\n"
            f"{summaries}\n\n"
            f"— Somewhere on the Internet, {today.strftime('%Y-%m-%d')}")

    if args.dry_run:
        msg_info(f"DRY_RUN: Would create a tag '{tag}' with message:\n{message}")
        return

    res = run_command(['git', 'tag', '-m', message, tag, 'HEAD'])
    logging.debug(res)
    msg_ok(f"Created tag '{tag}' with message:\n{message}")


def print_config(args, repo):
    """Print the values used for the release playbook"""
    print("\n--------------------------------\n"
          f"{fg.BOLD}Release:{fg.RESET}\n"
          f"  Component:        {repo}\n"
          f"  Version:          {args.version}\n"
          f"  Base branch:      {args.base}\n"
          f"  Semantic ver.:    {args.semver}")
    if args.semver:
        print(f"  Semver bump type: {args.semver_bump_type}")
    print("--------------------------------\n")


def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version",
                        help=f"Set the version for the release (by default, the latest tag will be auto-incremented)",
                        default=None)
    parser.add_argument("-t", "--token", help=f"Set the GitHub token")
    parser.add_argument("-b", "--base",
                        help=f"Set the release branch (Default: 'main')",
                        default='main')
    parser.add_argument("-d", "--debug", help="Print lots of debugging statements", action="store_const",
                        dest="loglevel", const=logging.DEBUG, default=logging.INFO)
    parser.add_argument("--semver",
                        action="store_true",
                        default=False,
                        help="Use semantic versioning, instead of a simple autoincrement. Note that only the first" + \
                        " number will be incremented and following numbers will be reset to zero.")
    parser.add_argument("--semver-bump-type",
                        action="store",
                        default="major",
                        choices=["major", "minor", "patch"],
                        help="When using semantic versioning, set the type of bump to perform.")
    parser.add_argument("--dry-run",
                        action="store_true",
                        default=False,
                        help="Don't actually create the tag, just print the values that would be used.")
    return parser

def main():
    """Main function"""
    parser = get_parser()
    global args
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel, format='%(asctime)s %(message)s', datefmt='%Y/%m/%d/ %H:%M:%S')

    # Get some basic fallback/default values
    repo = os.path.basename(os.getcwd())
    if args.version is None:
        latest_version = None
        try:
            # list all tags reachable from the current commit.
            tags = run_command(['git', 'tag', '-l', '--merged'], check=True).splitlines()
            if tags:
                versions = [Version(tag) for tag in tags]
                versions.sort()
                latest_version = str(versions[-1])
        except subprocess.CalledProcessError as e:
            logging.error("Failed to get the list of tags from the repository: %s", e.stderr)

        args.version = autoincrement_version(latest_version, args.semver, args.semver_bump_type)

    tag = f'v{args.version}'
    latest_tag = f"v{latest_version}" if latest_version is not None else ""
    logging.info("Current release: %s (tag: %s)", latest_version, latest_tag)
    logging.info("New release: %s (tag: %s)", args.version, tag)

    print_config(args, repo)

    # Create a release tag
    create_release_tag(args, repo, tag, latest_tag)

    # Don't push the tag if we're doing a dry run
    if args.dry_run:
        msg_info(f"DRY_RUN: Would push a tag {tag} to branch {args.base}")
        sys.exit(0)

    # Push the tag
    res = run_command(['git', 'push', 'origin', tag])
    logging.debug(res)
    msg_ok(f"Pushed tag '{tag}' to branch '{args.base}'")


if __name__ == "__main__":
    main()
