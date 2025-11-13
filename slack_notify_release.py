#!/usr/bin/env python3

"""
Script to send release notifications to Slack with threaded release notes.
"""

import argparse
import os
import re
import sys

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def format_changelog_for_slack(changelog: str) -> str:
    """
    Convert GitLab-flavored Markdown to Slack's mrkdwn format.

    Args:
        changelog: The changelog string with Markdown formatting

    Returns:
        Slack-formatted string
    """
    # Convert [text](url) to <url|text>
    slack_changelog = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<\2|\1>', changelog)

    # Convert #### headers to *bold* (Slack doesn't have headers)
    # Use ^ anchor with MULTILINE to only match headers at line start
    slack_changelog = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', slack_changelog, flags=re.MULTILINE)

    # Convert markdown list items (starting with * ) to Slack format (starting with - )
    slack_changelog = re.sub(r'^\s*\*\s+', '  - ', slack_changelog, flags=re.MULTILINE)

    return slack_changelog


def slack_notify_with_thread(
    message: str,
    slack_bot_token: str,
    slack_channel_id: str,
    thread_message: str | None = None,
    dry_run: bool = False
):
    """
    Send notifications to Slack channel and optionally post a threaded response.

    Args:
        message: The main message to post
        slack_bot_token: Slack bot OAuth token
        slack_channel_id: Slack channel ID to post to
        thread_message: Optional message to post as a threaded reply
        dry_run: If True, don't actually send to Slack, just print what would be sent

    Returns:
        tuple: (channel_id, message_ts) if successful, (None, None) otherwise
    """
    print(f"\n{'='*60}")
    print("MAIN MESSAGE:")
    print(f"{'='*60}")
    print(message)
    print(f"{'='*60}\n")

    if dry_run:
        print("🏜️  DRY RUN MODE - Not actually sending to Slack")
        if thread_message:
            formatted_thread_message = format_changelog_for_slack(thread_message)
            print(f"\n{'='*60}")
            print("THREADED RESPONSE (formatted for Slack):")
            print(f"{'='*60}")
            print(formatted_thread_message)
            print(f"{'='*60}\n")
        return "dry-run-channel", "dry-run-ts"

    if not slack_bot_token:
        print("No Slack bot token supplied.")
        return None, None

    if not slack_channel_id:
        print("No Slack channel ID supplied.")
        return None, None

    try:
        client = WebClient(token=slack_bot_token)

        # Post the main message
        response = client.chat_postMessage(
            channel=slack_channel_id,
            text=message
        )

        channel_id = response['channel']
        message_ts = response['ts']
        print(f"Posted message: {message_ts}")

        # Post threaded response if provided
        if thread_message:
            # Convert markdown to Slack format
            formatted_thread_message = format_changelog_for_slack(thread_message)

            thread_response = client.chat_postMessage(
                channel=channel_id,
                thread_ts=message_ts,
                text=formatted_thread_message
            )
            print(f"Posted threaded message: {thread_response['ts']}")

        return channel_id, message_ts

    except SlackApiError as e:
        print(f"Error posting to Slack: {e.response['error']}")
        return None, None


def main():
    """Send release notification to Slack with threaded release notes."""
    parser = argparse.ArgumentParser(
        description="Send release notifications to Slack with threaded release notes"
    )
    parser.add_argument(
        "--component",
        help="Name of the component being released",
        required=True
    )
    parser.add_argument(
        "--version",
        help="Version number of the release",
        required=True
    )
    parser.add_argument(
        "--release-notes-file",
        help="Path to file containing release notes",
        required=True
    )
    parser.add_argument(
        "--slack-bot-token",
        help="Slack bot OAuth token",
        required=True
    )
    parser.add_argument(
        "--slack-channel-id",
        help="Slack channel ID to post to",
        required=True
    )
    parser.add_argument(
        "--dry-run",
        help="Don't actually send to Slack, just print what would be sent",
        action="store_true",
        default=False
    )

    args = parser.parse_args()

    # Read release notes from file
    try:
        with open(args.release_notes_file, 'r', encoding='utf-8') as f:
            release_notes = f.read().strip()
    except FileNotFoundError:
        print(f"Error: Release notes file '{args.release_notes_file}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading release notes file: {e}")
        sys.exit(1)

    # Construct the main announcement message
    release_url = f"https://github.com/osbuild/{args.component}/releases/tag/v{args.version}"
    main_message = (
        f"🚀 *<{release_url}|{args.component} {args.version}>* "
        f"just got released upstream! 🚀"
    )

    # Send the notification with threaded release notes
    channel_id, message_ts = slack_notify_with_thread(
        main_message,
        args.slack_bot_token,
        args.slack_channel_id,
        release_notes,
        args.dry_run
    )

    if channel_id and message_ts:
        print("Release notification sent successfully!")
        sys.exit(0)
    else:
        print("Failed to send release notification.")
        sys.exit(1)


if __name__ == "__main__":
    main()

