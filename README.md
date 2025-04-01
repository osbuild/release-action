## release-action

This GitHub composite action is used for creating upstream releases in GitHub.
It does the following:

  * Extracts the release note text from the git tag
  * Creates a GitHub release based on the tag information
  * Automatically bumps the version (where applicable) and commits this change. If the version contains a dot, then the last part of the version is incremented.
