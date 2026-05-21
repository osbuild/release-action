#!/usr/bin/env python3

"""Release bot"""

import argparse
import re
import subprocess
import sys
import os
import logging
import time
from datetime import date
import requests
from packaging.version import Version

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
COMMIT_BATCH_SIZE = 50
GRAPHQL_MAX_RETRIES = 3
GRAPHQL_RETRY_DELAY = 2  # seconds, doubled each attempt


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


def graphql_request(token, query, variables=None):
    """Execute a GitHub GraphQL query and return the data dict."""
    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = requests.post(GITHUB_GRAPHQL_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    body = resp.json()

    if "errors" in body:
        for err in body["errors"]:
            msg_info(f"GraphQL error: {err.get('message', err)}")
        if "data" not in body or body["data"] is None:
            msg_error("GraphQL request failed with errors (see above).")

    return body["data"]


COMMIT_PR_FRAGMENT = """
fragment CommitPRInfo on Commit {
  oid
  associatedPullRequests(first: 10) {
    nodes {
      title
      number
      merged
      baseRefName
      author {
        login
        ... on User { name }
      }
      reviews(first: 20, states: APPROVED) {
        nodes {
          author {
            login
            ... on User { name }
          }
        }
      }
    }
  }
}
"""


def _build_commit_batch_query(hashes):
    """Build a GraphQL query that fetches PR info for a batch of commit hashes."""
    aliases = []
    for i, h in enumerate(hashes):
        aliases.append(f'  c{i}: object(expression: "{h}") {{ ...CommitPRInfo }}')
    fields = "\n".join(aliases)
    return f"""
query($owner: String!, $repo: String!) {{
  repository(owner: $owner, name: $repo) {{
{fields}
  }}
}}
{COMMIT_PR_FRAGMENT}
"""


def _extract_pr_from_commit(commit_data, base_branch, commit_hash):
    """
    Given the GraphQL result for a single commit alias, find the matching
    merged PR and return (title, number, author_name, reviewers).
    Returns None if no matching PR is found.
    """
    if commit_data is None:
        return None

    prs = commit_data.get("associatedPullRequests", {}).get("nodes", [])
    matched = [pr for pr in prs if pr.get("merged") and pr.get("baseRefName") == base_branch]

    if len(matched) != 1:
        if len(matched) > 1:
            msg_info(f"There are {len(matched)} pull requests associated with {commit_hash} - skipping...")
        return None

    pr = matched[0]
    author_info = pr.get("author") or {}
    author = author_info.get("name") or author_info.get("login") or "Nobody"
    login = author_info.get("login") or ""

    reviewers = []
    for review in pr.get("reviews", {}).get("nodes", []):
        r_author = review.get("author") or {}
        name = r_author.get("name") or r_author.get("login")
        if name:
            reviewers.append(name)

    reviewers = sorted(set(reviewers)) if reviewers else ["Nobody"]

    return {
        "title": pr["title"],
        "number": pr["number"],
        "author": author,
        "author_login": login,
        "reviewers": reviewers,
    }


def get_pullrequest_infos(args, repo, hashes):
    """Fetch PR titles for all commits using batched GraphQL queries."""
    logging.debug("Collect pull request titles...")
    summaries = []

    for batch_start in range(0, len(hashes), COMMIT_BATCH_SIZE):
        batch = hashes[batch_start:batch_start + COMMIT_BATCH_SIZE]
        batch_end = batch_start + len(batch)
        print(f"Fetching PRs for commits {batch_start + 1}-{batch_end}/{len(hashes)} via GraphQL...")

        query = _build_commit_batch_query(batch)
        variables = {"owner": "osbuild", "repo": repo}

        last_exc = None
        for attempt in range(GRAPHQL_MAX_RETRIES):
            try:
                data = graphql_request(args.token, query, variables)
                break
            except Exception as e:
                last_exc = e
                if attempt < GRAPHQL_MAX_RETRIES - 1:
                    delay = GRAPHQL_RETRY_DELAY * (2 ** attempt)
                    msg_info(f"GraphQL request failed (attempt {attempt + 1}/{GRAPHQL_MAX_RETRIES}): {e}")
                    msg_info(f"Retrying in {delay}s...")
                    time.sleep(delay)
        else:
            msg_error(
                f"GraphQL request failed after {GRAPHQL_MAX_RETRIES} attempts for commits "
                f"{batch_start + 1}-{batch_end}: {last_exc}"
            )

        repo_data = data.get("repository", {})
        for i, commit_hash in enumerate(batch):
            commit_data = repo_data.get(f"c{i}")
            pr_info = _extract_pr_from_commit(commit_data, args.base, commit_hash)
            if pr_info is None:
                continue

            pr_title_line = f"{pr_info['title']} (#{pr_info['number']})"
            author_reviewers_line = f"Author: {pr_info['author']}, Reviewers: {', '.join(pr_info['reviewers'])}"

            if pr_info["author_login"] == "dependabot[bot]" and pr_info["reviewers"] == ["github-actions[bot]"]:
                author_reviewers_line = "Automated dependency update"

            msg = (f"  - {pr_title_line}\n"
                   f"    - {author_reviewers_line}")
            summaries.append(msg)

    summaries = sorted(list(dict.fromkeys(summaries)))
    msg_ok(f"Collected summaries from {len(summaries)} pull requests ({len(hashes)} commits).")
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


def _parse_tag_message_entries(tag_message):
    """Parse PR entries from an annotated tag message."""
    entries = []
    current_entry = None
    for line in tag_message.strip().splitlines():
        if line.startswith("  - "):
            if current_entry is not None:
                entries.append(current_entry)
            current_entry = line
        elif line.startswith("    - ") and current_entry is not None:
            current_entry += "\n" + line
    if current_entry is not None:
        entries.append(current_entry)
    return entries


def fetch_images_changelogs(args, old_version, new_version):
    """
    Fetch annotated tag messages from osbuild/images for all releases
    in the range (old_version, new_version] using a single GraphQL query.
    """
    old_minor = old_version.minor
    new_minor = new_version.minor
    major = new_version.major

    version_tags = [f"v{major}.{minor}.0" for minor in range(old_minor + 1, new_minor + 1)]
    if not version_tags:
        return ""

    msg_info(f"Fetching images changelogs for {len(version_tags)} tags via GraphQL...")

    aliases = []
    for i, vtag in enumerate(version_tags):
        aliases.append(
            f'  t{i}: ref(qualifiedName: "refs/tags/{vtag}") {{\n'
            f'    target {{ ... on Tag {{ message }} }}\n'
            f'  }}'
        )
    fields = "\n".join(aliases)
    query = f"""
query {{
  repository(owner: "osbuild", name: "images") {{
{fields}
  }}
}}
"""

    try:
        data = graphql_request(args.token, query)
    except Exception as e:
        msg_info(f"GraphQL request for images changelogs failed: {e}")
        return ""

    repo_data = data.get("repository", {})
    all_entries = []
    for i, vtag in enumerate(version_tags):
        ref_data = repo_data.get(f"t{i}")
        if ref_data is None:
            msg_info(f"Could not fetch changelog for images {vtag}: tag not found")
            continue
        tag_message = ref_data.get("target", {}).get("message")
        if tag_message:
            all_entries.extend(_parse_tag_message_entries(tag_message))

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
