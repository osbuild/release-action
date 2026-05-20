#!/usr/bin/env python3

"""Release bot"""

import argparse
import re
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
                if reviewer is None:
                    msg_info(f"Couldn't get full name for {item['user']['login']}, using the username instead.")
                    reviewer = item['user']['login']
                reviewers.append(reviewer)

    if len(reviewers) == 0:
        reviewers.append("Nobody")

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
            if author is None:
                msg_info(f"Couldn't get full name for {username}, using the username instead.")
                author = username
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
            pr_title_line = f"{pull_request.title} (#{pull_request.number})"
            author_reviewers_line = f"Author: {author}, Reviewers: {', '.join(reviewers)}"

            if author == "dependabot[bot]" and reviewers == ["github-actions[bot]"]:
                author_reviewers_line = "Automated dependency update"

            msg = (f"  - {pr_title_line}\n"
                   f"    - {author_reviewers_line}")

            summaries.append(msg)

    # Deduplicate the list of pr summaries and sort it
    summaries = sorted(list(dict.fromkeys(summaries)))
    msg_ok(f"Collected summaries from {len(summaries)} pull requests ({i} commits).")
    return "\n".join(summaries)


def get_images_version_from_gomod(ref):
    """
    Parse the osbuild/images version from go.mod at a given git ref.
    Returns a Version object, or None if not found.
    """
    gomod = run_command(['git', 'show', f'{ref}:go.mod'])
    if not gomod or gomod.startswith('fatal:'):
        return None

    match = re.search(r'github\.com/osbuild/images\s+v(\S+)', gomod)
    if not match:
        return None

    try:
        return Version(match.group(1))
    except Exception:
        msg_info(f"Could not parse images version from go.mod at {ref}: {match.group(1)}")
        return None


def fetch_images_changelogs(args, old_version, new_version):
    """
    Fetch annotated tag messages from osbuild/images for all releases
    in the range (old_version, new_version] and return a single combined
    list of PR entries.
    """
    api = GhApi(repo="images", owner='osbuild', token=args.token)

    old_minor = old_version.minor
    new_minor = new_version.minor
    major = new_version.major

    all_entries = []
    for minor in range(old_minor + 1, new_minor + 1):
        version_tag = f"v{major}.{minor}.0"
        msg_info(f"Fetching images changelog for {version_tag}...")
        time.sleep(1)
        try:
            ref = api.git.get_ref(ref=f"tags/{version_tag}")
            tag_obj = api.git.get_tag(tag_sha=ref.object.sha)
            tag_message = tag_obj.message

            current_entry = None
            for line in tag_message.strip().splitlines():
                if line.startswith("  - "):
                    if current_entry is not None:
                        all_entries.append(current_entry)
                    current_entry = line
                elif line.startswith("    - ") and current_entry is not None:
                    current_entry += "\n" + line
            if current_entry is not None:
                all_entries.append(current_entry)
        except Exception as e:
            msg_info(f"Could not fetch changelog for images {version_tag}: {e}")

    all_entries = sorted(set(all_entries))
    return "\n".join(all_entries)


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

    images_section = ""
    if latest_tag:
        old_images = get_images_version_from_gomod(latest_tag)
        new_images = get_images_version_from_gomod("HEAD")
        if old_images and new_images and old_images != new_images:
            msg_info(f"osbuild/images changed: v{old_images} -> v{new_images}")
            images_changelog = fetch_images_changelogs(args, old_images, new_images)
            if images_changelog:
                images_section = (f"\n\nosbuild/images changes (v{old_images} -> v{new_images}):\n\n"
                                  f"{images_changelog}")
        elif not old_images and not new_images:
            logging.debug("No osbuild/images dependency found in go.mod, skipping changelog expansion.")

    tag = f'v{args.version}'
    message = (f"Changes with {args.version}\n\n"
            f"----------------\n"
            f"{summaries}"
            f"{images_section}\n\n"
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
    parser.add_argument("--remote",
                        default="origin",
                        help="Push the tag to this remote (default: 'origin').")
    return parser

def main():
    """Main function"""
    parser = get_parser()
    global args
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel, format='%(asctime)s %(message)s', datefmt='%Y/%m/%d/ %H:%M:%S')

    # Get some basic fallback/default values
    repo = os.path.basename(os.getcwd())

    latest_version = None
    try:
        # list all tags reachable from the current commit.
        tags = run_command(['git', 'tag', '-l', '--merged'], check=True).splitlines()
        if tags:
            versions = []
            for tag in tags:
                try:
                    versions.append(Version(tag))
                except Exception:
                    logging.debug("Skipping invalid tag: %s", tag)
            versions.sort()
            if versions:
                latest_version = str(versions[-1])
    except subprocess.CalledProcessError as e:
        logging.error("Failed to get the list of tags from the repository: %s", e.stderr)

    if args.version is None:
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
        msg_info(f"DRY_RUN: Would push a tag {tag} to remote {args.remote}")
        sys.exit(0)

    # Push the tag
    res = run_command(['git', 'push', args.remote, tag])
    logging.debug(res)
    msg_ok(f"Pushed tag '{tag}' to remote '{args.remote}'")


if __name__ == "__main__":
    main()
