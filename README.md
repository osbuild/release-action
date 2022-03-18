## release-action

This GitHub composite action is used for creating upstream git tags.
It does the following:

  * Bump the version number (based on the latest existing tag)
  * Extract the release note text from the pull requests associated with commits since the latest tag
  * Create a git tag with the release note text being the tag's body
  * Push the tag to the upstream repository
